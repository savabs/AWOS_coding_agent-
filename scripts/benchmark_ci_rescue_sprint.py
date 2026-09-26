#!/usr/bin/env python3
"""
benchmark_ci_rescue_sprint.py — Same 18 fixes: AWOS harness vs one-shot raw API.

Research: docs/research/ci_rescue_head_to_head.md
Fixture:  tests/fixtures/wedge_v1/ci_rescue_sprint (28 broken tests → should be 35 passing)

Usage:
    python3 scripts/benchmark_ci_rescue_sprint.py
    python3 scripts/benchmark_ci_rescue_sprint.py --dry-run
    python3 scripts/benchmark_ci_rescue_sprint.py --raw-only
    python3 scripts/benchmark_ci_rescue_sprint.py --awos-only
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "tests" / "fixtures" / "wedge_v1" / "ci_rescue_sprint"
PLAN_PATH = ROOT / "docs" / "missions" / "ci_rescue_sprint.plan.json"
MISSION_PATH = ROOT / "docs" / "missions" / "ci_rescue_sprint.json"
ASSERT_SCRIPT = ROOT / "scripts" / "wedge_v1_assert.py"
OUT_DIR = ROOT / ".awos" / "benchmarks"
PROOF_PATH = ROOT / "docs" / "product" / "ci_rescue_head_to_head.md"

SKIP_NAMES = {".git", ".awos", "__pycache__", "golden"}


@dataclass
class ArmResult:
    arm: str
    tasks_done: int
    tasks_total: int
    all_tests_pass: bool
    semantic_pass: bool
    cost_usd: float
    wall_sec: float
    error: str = ""


@dataclass
class HeadToHeadReport:
    timestamp: str
    raw: ArmResult
    awos: ArmResult

    @property
    def awos_cheaper(self) -> bool:
        if self.awos.cost_usd <= 0:
            return False
        return self.awos.cost_usd < self.raw.cost_usd

    @property
    def cost_ratio(self) -> float | None:
        if self.raw.cost_usd <= 0:
            return None
        return round(self.awos.cost_usd / self.raw.cost_usd, 3)


def _load_mission_env() -> dict[str, str]:
    cfg = json.loads(MISSION_PATH.read_text(encoding="utf-8"))
    env = dict(cfg.get("env", {}))
    # Benchmark runs in a temp copy — no worktree/session overhead
    env["AWOS_USE_WORKTREE"] = "false"
    env["AWOS_RUNTIME_SESSION"] = "false"
    env.setdefault("AWOS_LEARNING_DISABLE", "true")
    return env


def _apply_env(env: dict[str, str]) -> None:
    for k, v in env.items():
        os.environ[k] = str(v)


def _load_plan() -> list[dict[str, Any]]:
    from scaffold.agent.plan_actions import normalize_plan

    raw = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    return normalize_plan(raw if isinstance(raw, list) else raw.get("plan", []), ".")


def _copy_corpus(dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    def _ignore(directory: str, names: list[str]) -> list[str]:
        return [n for n in names if n in SKIP_NAMES or n.endswith(".pyc")]

    for item in FIXTURE.iterdir():
        if item.name in SKIP_NAMES:
            continue
        dest_item = dest / item.name
        if item.is_dir():
            shutil.copytree(item, dest_item, ignore=_ignore)
        else:
            shutil.copy2(item, dest_item)

    subprocess.run(["git", "init", "-q"], cwd=dest, check=True)
    subprocess.run(["git", "add", "-A"], cwd=dest, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "buggy baseline"], cwd=dest, check=True)


def _run_pytest(corpus: Path) -> bool:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=no"],
        cwd=str(corpus),
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def _run_wedge_assert(corpus: Path) -> bool:
    goal = FIXTURE / "wedge_goal.json"
    proc = subprocess.run(
        [sys.executable, str(ASSERT_SCRIPT), "--root", str(corpus), "--assertions", str(goal)],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0 and "SEMANTIC_PASS" in proc.stdout


def _apply_worker_edit(task: dict, file_path: Path, worker, verifier, tracker) -> tuple[bool, str]:
    content = file_path.read_text(encoding="utf-8")
    wr = worker.execute_task(
        task=task,
        file_content=content,
        codebase_context={"modules": "ci_rescue_sprint"},
        tracker=tracker,
        attempt=1,
    )
    if wr.get("success") and wr.get("_json_applied"):
        return True, ""
    if wr.get("success") and wr.get("search"):
        vr = verifier.verify_and_apply(
            {"search": wr["search"], "replace": wr["replace"], "task_spec": task},
            str(file_path),
        )
        if vr.get("success"):
            return True, ""
        return False, "; ".join(vr.get("errors") or [])[:200]
    return False, wr.get("error") or wr.get("reasoning") or "worker failed"


def _run_raw_arm(work_root: Path, plan: list[dict]) -> ArmResult:
    from scaffold.agent.token_tracker import TokenTracker
    from scaffold.agent.verifier import Verifier
    from scaffold.agent.worker import Worker

    corpus = work_root / "raw"
    _copy_corpus(corpus)
    tracker = TokenTracker(monthly_budget=float(os.getenv("AWOS_MONTHLY_BUDGET", "20")))
    worker = Worker()
    verifier = Verifier()

    cost_before = tracker.total_cost
    t0 = time.time()
    done = 0
    last_err = ""
    for task in plan:
        rel = task.get("path") or task.get("file")
        file_path = corpus / rel
        if not file_path.exists():
            last_err = f"missing file {rel}"
            continue
        ok, err = _apply_worker_edit(task, file_path, worker, verifier, tracker)
        if ok:
            done += 1
        elif err:
            last_err = err

    tests_pass = _run_pytest(corpus)
    semantic = _run_wedge_assert(corpus) if tests_pass else False
    if tests_pass and not semantic:
        last_err = last_err or "tests pass but wedge assert failed"

    return ArmResult(
        arm="raw",
        tasks_done=done,
        tasks_total=len(plan),
        all_tests_pass=tests_pass,
        semantic_pass=semantic,
        cost_usd=round(tracker.total_cost - cost_before, 6),
        wall_sec=round(time.time() - t0, 2),
        error=last_err if not tests_pass else "",
    )


def _run_awos_arm(work_root: Path, plan: list[dict], goal: str) -> ArmResult:
    from scaffold.agent.learning_policy import apply_kernel_defaults
    from scaffold.agent.orchestrator import Orchestrator
    from scaffold.agent.token_tracker import TokenTracker

    apply_kernel_defaults()
    corpus = work_root / "awos"
    _copy_corpus(corpus)
    tracker = TokenTracker(monthly_budget=float(os.getenv("AWOS_MONTHLY_BUDGET", "20")))
    orch = Orchestrator(tracker=tracker)

    cost_before = tracker.total_cost
    t0 = time.time()
    last_err = ""
    done = 0
    try:
        run = orch.execute_feature(
            goal=goal,
            codebase_root=str(corpus),
            pre_planned_tasks=plan,
        )
        done = int(run.get("tasks_completed", 0))
        if not run.get("success"):
            last_err = "; ".join(run.get("errors") or [])[:200] or "orchestrator failed"
    except Exception as exc:
        last_err = str(exc)[:200]

    tests_pass = _run_pytest(corpus)
    semantic = _run_wedge_assert(corpus) if tests_pass else False
    if tests_pass and not semantic:
        last_err = last_err or "tests pass but wedge assert failed"

    return ArmResult(
        arm="awos",
        tasks_done=done,
        tasks_total=len(plan),
        all_tests_pass=tests_pass,
        semantic_pass=semantic,
        cost_usd=round(tracker.total_cost - cost_before, 6),
        wall_sec=round(time.time() - t0, 2),
        error=last_err if not tests_pass else "",
    )


def _write_proof(report: HeadToHeadReport, json_path: Path) -> None:
    raw_ok = "yes" if report.raw.all_tests_pass else "no"
    awos_ok = "yes" if report.awos.all_tests_pass else "no"
    winner = "AWOS" if report.awos.all_tests_pass and (
        not report.raw.all_tests_pass or report.awos_cheaper
    ) else ("Raw API" if report.raw.all_tests_pass and not report.awos.all_tests_pass else "tie / mixed")

    ratio = report.cost_ratio
    ratio_line = f"AWOS cost is **{ratio:.0%}** of raw API cost." if ratio is not None else ""

    body = f"""# CI Rescue Sprint — AWOS vs Raw API

**Generated:** {report.timestamp}

Same 18 fix steps on the same broken practice repo (`ci_rescue_sprint`).

## Summary

| | Raw API (one try per step) | AWOS harness (verify + retry) |
|--|--|--|
| All 35 tests pass? | {raw_ok} | {awos_ok} |
| Honest pass check | {"yes" if report.raw.semantic_pass else "no"} | {"yes" if report.awos.semantic_pass else "no"} |
| Steps completed | {report.raw.tasks_done}/{report.raw.tasks_total} | {report.awos.tasks_done}/{report.awos.tasks_total} |
| Total API cost | ${report.raw.cost_usd:.4f} | ${report.awos.cost_usd:.4f} |
| Wall time | {report.raw.wall_sec:.1f}s | {report.awos.wall_sec:.1f}s |

{ratio_line}

**Winner (pass + cost):** {winner}

## What this means

- **Raw API** = send each fix prompt once, apply the patch, move on. Like chatting with the model 18 times with no safety net.
- **AWOS** = same 18 steps but the harness can retry when a patch breaks syntax or fails checks.

Raw JSON: `{json_path.relative_to(ROOT)}`

See also: `docs/ci_rescue_head_to_head_proof.md`
"""
    PROOF_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROOF_PATH.write_text(body, encoding="utf-8")


def run_benchmark(*, raw_only: bool = False, awos_only: bool = False, dry_run: bool = False) -> HeadToHeadReport:
    _apply_env(_load_mission_env())
    plan = _load_plan()
    goal = json.loads(MISSION_PATH.read_text(encoding="utf-8"))["goal"]

    print(f"\n{'='*64}")
    print("  CI Rescue Sprint — AWOS vs Raw API (18 steps, 28 broken tests)")
    print(f"{'='*64}\n")

    if dry_run:
        print(f"  Plan tasks: {len(plan)}")
        print(f"  Fixture:    {FIXTURE}")
        print("  [dry-run] no API calls")
        raise SystemExit(0)

    work_root = ROOT / ".awos" / "benchmark_ci_rescue"
    if work_root.exists():
        shutil.rmtree(work_root)
    work_root.mkdir(parents=True)

    raw = ArmResult("raw", 0, len(plan), False, False, 0.0, 0.0, "skipped")
    awos = ArmResult("awos", 0, len(plan), False, False, 0.0, 0.0, "skipped")

    if not awos_only:
        print("[1/2] Raw API arm — one model call per step, no retries...")
        raw = _run_raw_arm(work_root, plan)
        print(
            f"      tests={'PASS' if raw.all_tests_pass else 'FAIL'}  "
            f"cost=${raw.cost_usd:.4f}  time={raw.wall_sec:.1f}s  "
            f"steps={raw.tasks_done}/{raw.tasks_total}"
        )
        if raw.error:
            print(f"      note: {raw.error[:120]}")

    if not raw_only:
        label = "[2/2]" if not awos_only else "[1/1]"
        print(f"{label} AWOS harness arm — verify loop + retries...")
        awos = _run_awos_arm(work_root, plan, goal)
        print(
            f"      tests={'PASS' if awos.all_tests_pass else 'FAIL'}  "
            f"honest={'yes' if awos.semantic_pass else 'no'}  "
            f"cost=${awos.cost_usd:.4f}  time={awos.wall_sec:.1f}s  "
            f"steps={awos.tasks_done}/{awos.tasks_total}"
        )
        if awos.error:
            print(f"      note: {awos.error[:120]}")

    if raw_only or awos_only:
        print("\n  (partial run — use full benchmark for side-by-side report)")
        raise SystemExit(0)

    report = HeadToHeadReport(timestamp=datetime.now(timezone.utc).isoformat(), raw=raw, awos=awos)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = OUT_DIR / f"ci_rescue_head_to_head_{ts}.json"
    json_path.write_text(
        json.dumps(
            {
                "timestamp": report.timestamp,
                "raw": asdict(raw),
                "awos": asdict(awos),
                "awos_cheaper": report.awos_cheaper,
                "cost_ratio": report.cost_ratio,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_proof(report, json_path)

    print(f"\n{'─'*64}")
    if not awos_only and not raw_only:
        print(f"  Raw:  pass={raw.all_tests_pass}  ${raw.cost_usd:.4f}  {raw.wall_sec:.1f}s")
        print(f"  AWOS: pass={awos.all_tests_pass}  ${awos.cost_usd:.4f}  {awos.wall_sec:.1f}s")
        if report.cost_ratio is not None:
            print(f"  AWOS / Raw cost ratio: {report.cost_ratio:.2f}x")
    print(f"  Report: {PROOF_PATH}")
    print(f"  JSON:   {json_path}\n")
    return report


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="CI Rescue Sprint: AWOS vs raw API")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--raw-only", action="store_true")
    parser.add_argument("--awos-only", action="store_true")
    args = parser.parse_args()
    run_benchmark(raw_only=args.raw_only, awos_only=args.awos_only, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
