#!/usr/bin/env python3
"""
benchmark_clawcode_ci_rescue.py — clawcode: same 12 fixes, AWOS vs raw API.

Requires clawcode on branch awos-ci-rescue-buggy (12 failing tests):
    python3 scripts/clawcode_ci_rescue_prepare.py

Usage:
    python3 scripts/benchmark_clawcode_ci_rescue.py
    python3 scripts/benchmark_clawcode_ci_rescue.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
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

CLAWCODE = Path(os.environ.get("CLAWCODE_ROOT", "/home/becmachlean/2024/projects/clawcode"))
BUGGY_BRANCH = "awos-ci-rescue-buggy"
PLAN_PATH = ROOT / "docs" / "missions" / "clawcode_ci_rescue.plan.json"
MISSION_PATH = ROOT / "docs" / "missions" / "clawcode_ci_rescue.json"
GOAL_ASSERT = ROOT / "docs" / "missions" / "clawcode_ci_rescue_goal.json"
ASSERT_SCRIPT = ROOT / "scripts" / "wedge_v1_assert.py"
OUT_DIR = ROOT / ".awos" / "benchmarks"
PROOF_PATH = ROOT / "docs" / "product" / "clawcode_ci_rescue_head_to_head.md"

SKIP = {".git", ".awos", ".port_sessions", "__pycache__", ".pytest_cache"}


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
    def cost_ratio(self) -> float | None:
        if self.raw.cost_usd <= 0:
            return None
        return round(self.awos.cost_usd / self.raw.cost_usd, 3)


def _ensure_buggy_branch() -> None:
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=CLAWCODE,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if branch != BUGGY_BRANCH:
        print(f"Checking out {BUGGY_BRANCH} on clawcode...", flush=True)
        subprocess.run(["git", "checkout", BUGGY_BRANCH], cwd=CLAWCODE, check=True)
    code, failed, _ = _pytest_count(CLAWCODE)
    if code == 0 or failed < 8:
        print(
            f"Run first: python3 scripts/clawcode_ci_rescue_prepare.py (need 8+ failing tests, got {failed})",
            file=sys.stderr,
        )
        raise SystemExit(1)


def _pytest_count(corpus: Path) -> tuple[int, int, int]:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=no"],
        cwd=corpus,
        capture_output=True,
        text=True,
    )
    out = r.stdout + r.stderr
    failed = passed = 0
    m = re.search(r"(\d+) failed.*?(\d+) passed", out)
    if m:
        failed, passed = int(m.group(1)), int(m.group(2))
    return r.returncode, failed, passed


def _load_env() -> None:
    cfg = json.loads(MISSION_PATH.read_text(encoding="utf-8"))
    for k, v in cfg.get("env", {}).items():
        os.environ[k] = str(v)
    os.environ["AWOS_USE_WORKTREE"] = "false"
    os.environ["AWOS_RUNTIME_SESSION"] = "false"
    os.environ.setdefault("AWOS_LEARNING_DISABLE", "true")


def _load_plan() -> list[dict[str, Any]]:
    from scaffold.agent.plan_actions import normalize_plan

    raw = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    return normalize_plan(raw, ".")


def _copy_clawcode(dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    subprocess.run(
        [
            "rsync", "-a",
            "--exclude", ".git",
            "--exclude", ".awos",
            "--exclude", ".port_sessions",
            "--exclude", "__pycache__",
            "--exclude", ".pytest_cache",
            f"{CLAWCODE}/",
            f"{dest}/",
        ],
        check=True,
    )
    subprocess.run(["git", "init", "-q"], cwd=dest, check=True)
    subprocess.run(["git", "add", "-A"], cwd=dest, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "buggy baseline"], cwd=dest, check=True)


def _run_pytest(corpus: Path) -> bool:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=no"],
        cwd=corpus,
        capture_output=True,
    ).returncode == 0


def _run_assert(corpus: Path) -> bool:
    r = subprocess.run(
        [sys.executable, str(ASSERT_SCRIPT), "--root", str(corpus), "--assertions", str(GOAL_ASSERT)],
        capture_output=True,
        text=True,
    )
    return r.returncode == 0 and "SEMANTIC_PASS" in r.stdout


def _apply_worker_edit(task, file_path, worker, verifier, tracker) -> tuple[bool, str]:
    wr = worker.execute_task(
        task=task,
        file_content=file_path.read_text(encoding="utf-8"),
        codebase_context={"modules": "clawcode"},
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


def _run_raw(work_root: Path, plan: list[dict]) -> ArmResult:
    from scaffold.agent.token_tracker import TokenTracker
    from scaffold.agent.verifier import Verifier
    from scaffold.agent.worker import Worker

    corpus = work_root / "raw"
    _copy_clawcode(corpus)
    tracker = TokenTracker(monthly_budget=float(os.getenv("AWOS_MONTHLY_BUDGET", "20")))
    worker, verifier = Worker(), Verifier()
    cost_before = tracker.total_cost
    t0 = time.time()
    done, last_err = 0, ""
    for task in plan:
        fp = corpus / (task.get("path") or task.get("file"))
        if not fp.exists():
            last_err = f"missing {fp}"
            continue
        ok, err = _apply_worker_edit(task, fp, worker, verifier, tracker)
        if ok:
            done += 1
        elif err:
            last_err = err
    tests_ok = _run_pytest(corpus)
    sem = _run_assert(corpus) if tests_ok else False
    return ArmResult(
        "raw", done, len(plan), tests_ok, sem,
        round(tracker.total_cost - cost_before, 6),
        round(time.time() - t0, 2),
        last_err if not tests_ok else "",
    )


def _run_awos(work_root: Path, plan: list[dict], goal: str) -> ArmResult:
    from scaffold.agent.learning_policy import apply_kernel_defaults
    from scaffold.agent.orchestrator import Orchestrator
    from scaffold.agent.token_tracker import TokenTracker

    apply_kernel_defaults()
    corpus = work_root / "awos"
    _copy_clawcode(corpus)
    tracker = TokenTracker(monthly_budget=float(os.getenv("AWOS_MONTHLY_BUDGET", "20")))
    orch = Orchestrator(tracker=tracker)
    cost_before = tracker.total_cost
    t0 = time.time()
    last_err, done = "", 0
    try:
        run = orch.execute_feature(goal=goal, codebase_root=str(corpus), pre_planned_tasks=plan)
        done = int(run.get("tasks_completed", 0))
        if not run.get("success"):
            last_err = "; ".join(run.get("errors") or [])[:200] or "orchestrator failed"
    except Exception as exc:
        last_err = str(exc)[:200]
    tests_ok = _run_pytest(corpus)
    sem = _run_assert(corpus) if tests_ok else False
    return ArmResult(
        "awos", done, len(plan), tests_ok, sem,
        round(tracker.total_cost - cost_before, 6),
        round(time.time() - t0, 2),
        last_err if not tests_ok else "",
    )


def _write_proof(report: HeadToHeadReport, json_path: Path) -> None:
    raw_ok = "yes" if report.raw.all_tests_pass else "no"
    awos_ok = "yes" if report.awos.all_tests_pass else "no"
    ratio = report.cost_ratio

    if report.awos.all_tests_pass and not report.raw.all_tests_pass:
        headline = "AWOS finished the job. Raw API did not — even though raw was cheaper."
    elif report.awos.all_tests_pass and report.raw.all_tests_pass and ratio and ratio < 1:
        headline = f"Both passed. AWOS was cheaper ({ratio:.0%} of raw cost)."
    elif report.awos.all_tests_pass and report.raw.all_tests_pass:
        headline = "Both passed. Compare cost and time below."
    else:
        headline = "Mixed result — see table."

    body = f"""# Clawcode CI Rescue — AWOS vs Raw API

**Generated:** {report.timestamp}

Real repo **clawcode**, branch `awos-ci-rescue-buggy`, same 12 fix steps.

## Summary

| | Raw API (one try per step) | AWOS harness |
|--|--|--|
| All 22 tests pass? | {raw_ok} | {awos_ok} |
| Honest pass check | {"yes" if report.raw.semantic_pass else "no"} | {"yes" if report.awos.semantic_pass else "no"} |
| Steps completed | {report.raw.tasks_done}/{report.raw.tasks_total} | {report.awos.tasks_done}/{report.awos.tasks_total} |
| API cost | ${report.raw.cost_usd:.4f} | ${report.awos.cost_usd:.4f} |
| Time | {report.raw.wall_sec:.1f}s | {report.awos.wall_sec:.1f}s |

## Plain English

{headline}

- **Raw API** = 12 separate model calls, no retry loop.
- **AWOS** = same 12 steps with verify and retry.

Raw JSON: `{json_path.relative_to(ROOT)}`
"""
    PROOF_PATH.write_text(body, encoding="utf-8")


def run_benchmark(*, dry_run: bool = False) -> HeadToHeadReport:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    _ensure_buggy_branch()
    _load_env()
    plan = _load_plan()
    goal = json.loads(MISSION_PATH.read_text(encoding="utf-8"))["goal"]

    print(f"\n{'='*64}")
    print("  Clawcode CI Rescue — AWOS vs Raw API (12 steps, real repo)")
    print(f"{'='*64}\n")

    if dry_run:
        _, failed, passed = _pytest_count(CLAWCODE)
        print(f"  clawcode: {CLAWCODE}")
        print(f"  branch:   {BUGGY_BRANCH}")
        print(f"  tests:    {failed} failed, {passed} passed")
        print(f"  plan:     {len(plan)} tasks")
        raise SystemExit(0)

    work_root = ROOT / ".awos" / "benchmark_clawcode_ci_rescue"
    if work_root.exists():
        shutil.rmtree(work_root)
    work_root.mkdir(parents=True)

    print("[1/2] Raw API...")
    raw = _run_raw(work_root, plan)
    print(f"      tests={'PASS' if raw.all_tests_pass else 'FAIL'}  ${raw.cost_usd:.4f}  {raw.wall_sec:.1f}s")

    print("[2/2] AWOS harness...")
    awos = _run_awos(work_root, plan, goal)
    print(f"      tests={'PASS' if awos.all_tests_pass else 'FAIL'}  ${awos.cost_usd:.4f}  {awos.wall_sec:.1f}s")

    report = HeadToHeadReport(timestamp=datetime.now(timezone.utc).isoformat(), raw=raw, awos=awos)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = OUT_DIR / f"clawcode_ci_rescue_head_to_head_{ts}.json"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps({
            "timestamp": report.timestamp,
            "repo": str(CLAWCODE),
            "branch": BUGGY_BRANCH,
            "raw": asdict(raw),
            "awos": asdict(awos),
            "cost_ratio": report.cost_ratio,
        }, indent=2),
        encoding="utf-8",
    )
    _write_proof(report, json_path)

    print(f"\n{'─'*64}")
    print(f"  Raw:  pass={raw.all_tests_pass}  ${raw.cost_usd:.4f}")
    print(f"  AWOS: pass={awos.all_tests_pass}  ${awos.cost_usd:.4f}")
    if report.cost_ratio:
        print(f"  AWOS / Raw cost: {report.cost_ratio:.2f}x")
    print(f"  Report: {PROOF_PATH}\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run_benchmark(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
