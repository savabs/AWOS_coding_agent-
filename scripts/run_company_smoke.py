#!/usr/bin/env python3
"""
Company smoke — single ReAct session on failing tests (Claude Code style).

Bypasses planner/decomposer; exercises backend only:
  read tests → edit buggy.py → run_tests → finish
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests/fixtures/benchmark/02_wrong_logic"

# Load .env from repo root when present
_env = REPO / ".env"
if _env.exists():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="awos_company_smoke_"))
    print(f"[SMOKE] Work dir: {work}")

    shutil.copy(FIXTURE / "buggy.py", work / "buggy.py")
    shutil.copy(FIXTURE / "test_case.py", work / "test_case.py")
    (work / "pytest.ini").write_text(
        "[pytest]\ntestpaths = .\npython_files = test_case.py\naddopts = -q --tb=short\n",
        encoding="utf-8",
    )

    # Baseline must fail
    base = subprocess.run(
        [sys.executable, "-m", "pytest", "test_case.py", "-q"],
        cwd=work,
        capture_output=True,
        text=True,
    )
    if base.returncode == 0:
        print("[SMOKE] ERROR: fixture already passes")
        return 1
    print("[SMOKE] Baseline: tests fail OK")

    os.environ.setdefault("AWOS_REACT_WORKER", "1")
    os.environ.setdefault("AWOS_SAFE_TO_RUN_TESTS", "1")
    os.environ.setdefault("AWOS_REACT_MAX_TURNS", "18")

    sys.path.insert(0, str(REPO))
    from scaffold.agent.react_worker import ReActWorker

    task = {
        "task_id": 1,
        "file": "buggy.py",
        "action": (
            "test_case.py tests are failing against buggy.py. "
            "Read test_case.py first. Fix ONLY buggy.py so all tests pass. "
            "Use run_tests with paths=test_case.py to verify. Do not edit test_case.py."
        ),
        "complexity": "medium",
    }

    buggy_content = (work / "buggy.py").read_text(encoding="utf-8")
    worker = ReActWorker()
    print("[SMOKE] Starting ReAct worker…")
    result = worker.execute_task(
        task=task,
        file_content=buggy_content,
        codebase_context={"architecture": "single-module Python", "files": ["buggy.py", "test_case.py"]},
        project_root=str(work),
    )

    print(f"[SMOKE] ReAct success={result.get('success')} summary={result.get('summary', '')[:200]}")
    if not result.get("success"):
        print(f"[SMOKE] Error: {result.get('error')}")
        for i, step in enumerate(result.get("steps") or [], 1):
            print(f"  step {i}: {step.action} ok={step.success} obs={step.observation[:120]}")
    tr = result.get("test_result")
    if tr is not None:
        print(
            f"[SMOKE] Agent test_result: passed={tr.passed} failed={tr.failed} "
            f"no_tests={tr.no_tests_found}"
        )

    verify = subprocess.run(
        [sys.executable, "-m", "pytest", "test_case.py", "-v"],
        cwd=work,
        capture_output=True,
        text=True,
    )
    print(verify.stdout)
    if verify.stderr:
        print(verify.stderr, file=sys.stderr)

    if verify.returncode == 0:
        print("\n[SMOKE] ✓ COMPANY SMOKE PASSED — pytest green")
        print((work / "buggy.py").read_text(encoding="utf-8"))
        return 0

    print("\n[SMOKE] ✗ COMPANY SMOKE FAILED — pytest still red")
    print((work / "buggy.py").read_text(encoding="utf-8"))
    if os.getenv("KEEP_WORK_DIR") == "1":
        print(f"[SMOKE] Kept: {work}")
    else:
        shutil.rmtree(work, ignore_errors=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
