"""
demo_runner.py — a model-free host runner for app demos and tests.

    AWOS_HOST_RUNNER=scaffold.agent.app.demo_runner:run awos serve --worker
    AWOS_APP_DEMO_FIX='{"file": "calc.py", "old": "- 1", "new": ""}'

It works exactly like the real runner from the worker's point of view (own
git worktree at the base commit, host-verified tests, hand-off branch), but
the "agent" is a scripted string replacement, so it costs nothing. It writes
log lines in the orchestrator's format, so the app's progress view is real.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


def run(job, ctx) -> dict:
    from scaffold.agent.host.worker import prepare_workspace, verify_tests

    step = float(os.getenv("AWOS_APP_DEMO_STEP_SEC", "0.3"))
    workspace, base = prepare_workspace(job, ctx.job_dir)
    ctx.step("workspace", path=workspace, base_commit=base)
    fix = json.loads(os.getenv("AWOS_APP_DEMO_FIX", "{}") or "{}")
    log = ctx.job_dir / "worker.log"

    def emit(line: str) -> None:
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        time.sleep(step)

    emit("[EXPLORE] Pre-plan repository scan...")
    emit("[PLANNER] skipped: small goal — running as one task")
    target = fix.get("file", "")
    if target:
        emit(f"[agent] t1 read_file({target}) -> ok 0.0s")
    emit("[agent] t1 run_tests() -> ok 0.2s")
    changed = 0
    if target:
        path = Path(workspace) / target
        text = path.read_text(encoding="utf-8")
        if fix.get("old", "") in text:
            path.write_text(text.replace(fix["old"], fix.get("new", ""), 1), encoding="utf-8")
            changed = 1
        emit(f"[agent] t2 edit_file({target}) -> ok 0.0s")
    tests = verify_tests(workspace) if base else None
    if tests:
        emit(f"[TASK 1] Tests: {tests['passed']} passed, {tests['failed']} failed")
        ctx.step("verify_tests", **tests)
    ok = bool(changed) and (tests is None or tests.get("status") == "passed")
    return {"success": ok, "tests": tests, "cost_usd": 0.0, "workspace": workspace,
            "base_commit": base, "error": None if ok else "the scripted fix did not apply"}
