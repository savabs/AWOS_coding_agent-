"""
swebench_lite.py — SWE-bench Lite subset harness for AWOS ReAct worker.

Workflow (matches industry standard):
  1. Load instances from HuggingFace SWE-bench_Lite
  2. Clone repo @ base_commit, run ReAct on problem_statement
  3. Export model_patch (git diff, test files reset per Devin/Aider methodology)
  4. Optional: evaluate via swebench.harness (Docker)

See: https://www.swebench.com/SWE-bench/guides/evaluation/
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

DEFAULT_DATASET = "princeton-nlp/SWE-bench_Lite"
DEFAULT_SPLIT = "test"
DEFAULT_MODEL_NAME = "awos-react"
SUBSET_FILE = Path(__file__).resolve().parents[2] / "tests/fixtures/swebench_lite/subset_10.txt"


@dataclass
class SweBenchInstance:
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    test_patch: str = ""
    hints_text: str = ""
    version: str = ""

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> SweBenchInstance:
        return cls(
            instance_id=str(row["instance_id"]),
            repo=str(row["repo"]),
            base_commit=str(row["base_commit"]),
            problem_statement=str(row["problem_statement"]),
            test_patch=str(row.get("test_patch") or ""),
            hints_text=str(row.get("hints_text") or ""),
            version=str(row.get("version") or ""),
        )


@dataclass
class InstanceRunResult:
    instance_id: str
    success: bool
    model_patch: str = ""
    react_success: bool = False
    files_changed: list[str] = field(default_factory=list)
    error: str = ""
    latency_sec: float = 0.0
    work_dir: str = ""
    model_used: str = ""
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    n_turns: int = 0

    def to_prediction(self, model_name: str = DEFAULT_MODEL_NAME) -> dict[str, str]:
        return {
            "instance_id": self.instance_id,
            "model_name_or_path": model_name,
            "model_patch": self.model_patch,
        }


def load_default_subset_ids() -> list[str]:
    if not SUBSET_FILE.is_file():
        return []
    ids: list[str] = []
    for line in SUBSET_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            ids.append(line)
    return ids


def load_lite_instances(
    *,
    split: str = DEFAULT_SPLIT,
    dataset_name: str = DEFAULT_DATASET,
    instance_ids: Optional[list[str]] = None,
    limit: Optional[int] = None,
    use_default_subset: bool = True,
) -> list[SweBenchInstance]:
    """Load SWE-bench Lite rows from HuggingFace datasets."""
    try:
        from datasets import load_dataset
    except ImportError as exc:
        raise ImportError("pip install datasets") from exc

    ds = load_dataset(dataset_name, split=split)
    rows = [SweBenchInstance.from_row(dict(r)) for r in ds]

    if instance_ids:
        wanted = set(instance_ids)
        rows = [r for r in rows if r.instance_id in wanted]
        order = {iid: n for n, iid in enumerate(instance_ids)}
        rows.sort(key=lambda r: order.get(r.instance_id, 9999))
    elif use_default_subset:
        subset = load_default_subset_ids()
        if subset:
            wanted = set(subset)
            rows = [r for r in rows if r.instance_id in wanted]
            order = {iid: n for n, iid in enumerate(subset)}
            rows.sort(key=lambda r: order.get(r.instance_id, 9999))

    if limit is not None:
        rows = rows[:limit]
    return rows


def parse_test_patch_paths(test_patch: str) -> list[str]:
    """Paths touched by the gold test_patch (exclude from model_patch)."""
    if not test_patch:
        return []
    paths = re.findall(r"diff --git a/(.*?) b/", test_patch)
    return sorted(set(paths))


def clone_instance_repo(instance: SweBenchInstance, work_dir: Path, *, timeout_sec: int = 900) -> None:
    """Clone GitHub repo and checkout base_commit; strip remote to reduce leakage."""
    work_dir.mkdir(parents=True, exist_ok=True)
    if (work_dir / ".git").is_dir():
        return
    url = f"https://github.com/{instance.repo}.git"
    subprocess.run(
        ["git", "clone", url, str(work_dir)],
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
    )
    subprocess.run(
        ["git", "checkout", instance.base_commit],
        cwd=work_dir,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    subprocess.run(["git", "remote", "remove", "origin"], cwd=work_dir, capture_output=False)
    subprocess.run(["git", "config", "user.email", "awos@local"], cwd=work_dir, check=False)
    subprocess.run(["git", "config", "user.name", "AWOS"], cwd=work_dir, check=False)


def extract_model_patch(work_dir: Path, test_patch: str) -> str:
    """
    Git diff after resetting test files (SWE-bench / Devin grading convention).
    """
    for rel in parse_test_patch_paths(test_patch):
        subprocess.run(
            ["git", "checkout", "HEAD", "--", rel],
            cwd=work_dir,
            capture_output=True,
            text=True,
        )
    proc = subprocess.run(
        ["git", "diff", "HEAD"],
        cwd=work_dir,
        capture_output=True,
        text=True,
    )
    return proc.stdout or ""


def build_task_action(instance: SweBenchInstance, *, include_hints: bool = False) -> str:
    parts = [
        "Fix this GitHub issue in the repository. Edit source files only.",
        "Do not modify test files. Run existing tests when possible, then finish.",
        "",
        instance.problem_statement.strip(),
    ]
    if include_hints and instance.hints_text.strip():
        parts.extend(["", "Hints:", instance.hints_text.strip()])
    return "\n".join(parts)


def run_instance_react(
    instance: SweBenchInstance,
    work_dir: Path,
    *,
    max_turns: int = 40,
    include_hints: bool = False,
) -> InstanceRunResult:
    """Run AWOS agent on one SWE-bench Lite instance.

    Uses EscalationEngine for model routing (not a fixed model).
    Uses GuiEventBus for visibility (prints model, cost, thoughts).
    Uses the full ReAct worker with bash-first editing.
    """
    t0 = time.time()
    result = InstanceRunResult(instance_id=instance.instance_id, success=False, work_dir=str(work_dir))

    os.environ.setdefault("AWOS_REACT_WORKER", "1")
    os.environ["AWOS_SAFE_TO_RUN_TESTS"] = ""  # Docker handles test validation
    os.environ.setdefault("AWOS_REPO_MAP", "1")
    os.environ["ALLOW_SHELL"] = "1"  # Enable bash editing (sed, cat, python -c)
    os.environ["AWOS_REACT_MAX_TURNS"] = str(max_turns)
    # Allow full model routing — don't restrict to cheap-only for SWE-bench
    os.environ["AWOS_CHEAP_ONLY"] = ""
    os.environ["AWOS_PREMIUM_BUDGET"] = "20"  # Allow premium models (Claude, etc.)
    # Allow shell commands for pip install, etc.
    os.environ["ALLOW_CODE_EXEC"] = "1"

    try:
        from scaffold.agent.escalation_engine import EscalationEngine, EscalationLevel, ModelSpec
        from scaffold.agent.gui_events import EventType, GuiEventBus
        from scaffold.agent.react_worker import ReActWorker
    except ImportError:
        from escalation_engine import EscalationEngine  # type: ignore
        from gui_events import GuiEventBus  # type: ignore
        from react_worker import ReActWorker  # type: ignore

    action = build_task_action(instance, include_hints=include_hints)
    primary = _guess_primary_file(work_dir, instance.problem_statement)
    snippet = ""
    if primary and (work_dir / primary).is_file():
        snippet = (work_dir / primary).read_text(encoding="utf-8", errors="replace")[:8000]

    # ── EscalationEngine: pick the RIGHT model for this task ─────────────
    escalation = EscalationEngine()
    task_dict = {
        "task_id": instance.instance_id,
        "file": primary or "README.md",
        "action": action,
        "complexity": "high",  # SWE-bench tasks are complex
    }
    esc_decision = escalation.decide(
        task=task_dict,
        failure_count=0,
        budget_remaining=20.0,
        dead_providers=set(),
    )
    model_spec = esc_decision.spec
    print(f"[SWEBENCH] Model routed: {model_spec.name} ({model_spec.provider})")
    print(f"[SWEBENCH] Reason: {esc_decision.reason}")
    print(f"[SWEBENCH] Cost estimate: ${model_spec.cost_per_req:.4f}/req")

    # ── GuiEventBus: stream events for visibility ────────────────────────
    bus = GuiEventBus(f"swebench_{instance.instance_id}", goal=action)
    bus.emit_session_start(action, str(work_dir))
    bus.emit_model_routed(
        model_name=model_spec.name,
        tier=f"T{model_spec.level.value}",
        reason=esc_decision.reason,
        cost_estimate=model_spec.cost_per_req,
        provider=model_spec.provider,
    )

    # ── ReAct step callback: stream thinking + tool calls ────────────────
    react_turn = 0

    def on_step(thought, action_name, action_input, observation, success, latency_ms):
        nonlocal react_turn
        react_turn += 1
        if thought and len(thought) > 5:
            bus.emit_agent_thinking(thought, react_turn)
        bus.emit_agent_tool_call(action_name, action_input, observation, success, latency_ms, react_turn)
        # Print to console for immediate visibility
        status = "✓" if success else "✗"
        print(f"  [{react_turn}] {status} {action_name}: {thought[:80]}")

    # ── Run with DeepSeek (only working model) ──────────────────────────
    worker = ReActWorker()
    react = worker.execute_task(
        task=task_dict,
        file_content=snippet,
        codebase_context={
            "architecture": f"SWE-bench Lite: {instance.repo}",
            "files": [],
        },
        project_root=str(work_dir),
        model_spec=model_spec,
        step_callback=on_step,
    )

    # ── Record outcome ───────────────────────────────────────────────────
    result.react_success = bool(react.get("files_changed"))
    result.files_changed = list(react.get("files_changed") or [])
    result.model_patch = extract_model_patch(work_dir, instance.test_patch)
    result.success = bool(result.model_patch.strip())

    cost = float(react.get("cost_usd", 0.0))
    model_used = react.get("model_used", model_spec.name)
    tokens_in = int(react.get("input_tokens", 0))
    tokens_out = int(react.get("output_tokens", 0))

    if not result.success:
        result.error = react.get("error") or "empty patch"
        if result.files_changed and not result.model_patch.strip():
            result.error = f"Files changed ({result.files_changed}) but git diff is empty"

    result.latency_sec = time.time() - t0
    result.model_used = model_used
    result.cost_usd = cost
    result.input_tokens = tokens_in
    result.output_tokens = tokens_out
    result.n_turns = react_turn

    # Emit final events
    bus.emit_task_complete(
        task_id=0,
        success=result.react_success,
        cost_usd=cost,
        n_edits=len(result.files_changed),
        error=result.error,
    )
    bus.emit_session_done(
        completed=1 if result.react_success else 0,
        failed=0 if result.react_success else 1,
        total_cost=cost,
        elapsed=result.latency_sec,
    )

    # Print summary
    print(f"[SWEBENCH] Result: {'✅ SUCCESS' if result.react_success else '❌ FAIL'}")
    print(f"[SWEBENCH] Model: {model_used} | Cost: ${cost:.4f} | Tokens: {tokens_in}in/{tokens_out}out")
    print(f"[SWEBENCH] Patch: {len(result.model_patch)}B | Turns: {react_turn} | Time: {result.latency_sec:.1f}s")

    return result


def _guess_primary_file(work_dir: Path, problem: str) -> str:
    """Heuristic primary file hint for repo map (optional)."""
    m = re.search(r"`([^`]+\.py)`", problem)
    if m:
        candidate = m.group(1)
        if (work_dir / candidate).is_file():
            return candidate
    py_files = sorted(work_dir.rglob("*.py"))
    for p in py_files:
        if "test" not in p.name.lower() and p.stat().st_size < 50_000:
            try:
                return str(p.relative_to(work_dir))
            except ValueError:
                continue
    return ""


def write_predictions_jsonl(predictions: Iterable[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for pred in predictions:
            fh.write(json.dumps(pred, ensure_ascii=False) + "\n")


def write_run_report(results: list[InstanceRunResult], path: Path, *, model_name: str) -> dict[str, Any]:
    total_cost = sum(r.cost_usd for r in results)
    total_tokens_in = sum(r.input_tokens for r in results)
    total_tokens_out = sum(r.output_tokens for r in results)
    models_used = {}
    for r in results:
        m = r.model_used or "unknown"
        models_used[m] = models_used.get(m, 0) + 1

    report = {
        "model_name_or_path": model_name,
        "total": len(results),
        "patches_submitted": sum(1 for r in results if r.model_patch.strip()),
        "react_reported_success": sum(1 for r in results if r.react_success),
        "total_cost_usd": round(total_cost, 4),
        "total_input_tokens": total_tokens_in,
        "total_output_tokens": total_tokens_out,
        "models_used": models_used,
        "avg_latency_sec": round(sum(r.latency_sec for r in results) / max(len(results), 1), 1),
        "instances": [asdict(r) for r in results],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def run_swebench_evaluation(
    predictions_path: Path,
    *,
    dataset_name: str = DEFAULT_DATASET,
    run_id: str = "awos_lite_subset",
    max_workers: int = 4,
    instance_ids: Optional[list[str]] = None,
) -> subprocess.CompletedProcess[str]:
    """Run official SWE-bench Docker harness (requires: pip install swebench, Docker)."""
    cmd = [
        sys.executable,
        "-m",
        "swebench.harness.run_evaluation",
        "--dataset_name",
        dataset_name,
        "--predictions_path",
        str(predictions_path),
        "--max_workers",
        str(max_workers),
        "--run_id",
        run_id,
    ]
    if instance_ids:
        cmd.extend(["--instance_ids", *instance_ids])
    return subprocess.run(cmd, capture_output=True, text=True)


def format_manifest(instances: list[SweBenchInstance]) -> str:
    lines = [f"SWE-bench Lite subset: {len(instances)} instance(s)", ""]
    for inst in instances:
        lines.append(f"  {inst.instance_id}  ({inst.repo} @ {inst.base_commit[:8]})")
    return "\n".join(lines)
