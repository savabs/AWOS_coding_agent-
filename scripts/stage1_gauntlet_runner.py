#!/usr/bin/env python3
"""
stage1_gauntlet_runner.py — Execute Stage 1 risk gauntlet scenarios.

Booklet: docs/stage1_risk_gauntlet_booklet.md
Scenarios: docs/gauntlet_scenarios.json

Usage:
    python3 scripts/stage1_gauntlet_runner.py list
    python3 scripts/stage1_gauntlet_runner.py run G2
    python3 scripts/stage1_gauntlet_runner.py run G3
    python3 scripts/stage1_gauntlet_runner.py run-all
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCENARIOS_PATH = ROOT / "docs" / "gauntlet_scenarios.json"
MCTS_TRACES = ROOT / ".awos" / "mcts_traces.jsonl"
ERROR_PATTERNS = ROOT / ".awos" / "error_patterns.jsonl"
GAUNTLET_BROKEN_MODULE = ROOT / "tests" / "fixtures" / "gauntlet_broken_module.py"
GAUNTLET_BROKEN_SOURCE = '''"""Intentional bug fixture for G3 failure-hunt gauntlet. Do not use in production paths."""


def broken_add(a: int, b: int) -> int:
    """Return sum of a and b — currently wrong for gauntlet stress tests."""
    return a - b  # BUG: gauntlet G3 expects worker/MCTS to fix to a + b
'''


def _load_suite() -> dict[str, Any]:
    return json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))


def _apply_defaults(env: dict[str, str] | None = None) -> dict[str, str]:
    suite = _load_suite()
    out = os.environ.copy()
    for k, v in suite.get("defaults", {}).items():
        out.setdefault(k, v)
    if env:
        out.update(env)
    out.setdefault("AWOS_LEARNING_DISABLE", "true")
    return out


def list_scenarios() -> None:
    suite = _load_suite()
    print("\n┌─ STAGE 1 RISK GAUNTLET ─────────────────────────────────")
    print(f"│  Booklet: docs/stage1_risk_gauntlet_booklet.md")
    print("│")
    for s in suite.get("scenarios", []):
        manual = " (manual review)" if s.get("manual_review") else ""
        print(f"│  {s['id']:<4} {s['name']:<22} [{s.get('type', '?')}]{manual}")
    print("│")
    print("│  Run:  awos gauntlet run G2")
    print("│        python3 awos.py gauntlet run G2")
    print("│  Help: python3 awos.py guide")
    print("└──────────────────────────────────────────────────────────\n")


def _mcts_trace_count() -> int:
    if not MCTS_TRACES.exists():
        return 0
    return sum(1 for line in MCTS_TRACES.read_text().splitlines() if line.strip())


def _error_pattern_count() -> int:
    if not ERROR_PATTERNS.exists():
        return 0
    return sum(1 for line in ERROR_PATTERNS.read_text().splitlines() if line.strip())


def _last_error_pattern() -> Optional[dict[str, Any]]:
    if not ERROR_PATTERNS.exists():
        return None
    lines = [ln for ln in ERROR_PATTERNS.read_text().splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return None


def _reset_gauntlet_broken_module() -> None:
    GAUNTLET_BROKEN_MODULE.parent.mkdir(parents=True, exist_ok=True)
    GAUNTLET_BROKEN_MODULE.write_text(GAUNTLET_BROKEN_SOURCE, encoding="utf-8")


def run_g3_mcts_failure() -> dict[str, Any]:
    """G3: force worker failure → MCTS fallback → mcts_traces.jsonl line."""
    os.chdir(ROOT)
    _reset_gauntlet_broken_module()
    env = _apply_defaults({"AWOS_E2E": "1", "AWOS_MCTS_ON_LOW": "true"})
    if not env.get("ANTHROPIC_API_KEY"):
        env["ANTHROPIC_API_KEY"] = "fake-key-for-gauntlet"
    for k, v in env.items():
        os.environ[k] = v

    from unittest.mock import patch

    from scaffold.agent.mcts_search import MCTSResult
    from scaffold.agent.orchestrator import Orchestrator

    plan = [
        {
            "task_id": 1,
            "task_type": "edit_file",
            "path": "tests/fixtures/gauntlet_broken_module.py",
            "file": "tests/fixtures/gauntlet_broken_module.py",
            "action": "Fix broken_add to return a+b not a-b",
            "complexity": "medium",
        }
    ]

    mcts_before = _mcts_trace_count()
    patterns_before = _error_pattern_count()
    orch = Orchestrator()

    def fail_worker(*_args, **_kwargs):
        return {"success": False, "error": "gauntlet G3 forced worker failure"}

    mock_mcts_result = MCTSResult(
        success=True,
        search="return a - b  # BUG: gauntlet G3 expects worker/MCTS to fix to a + b",
        replace="return a + b  # fixed by gauntlet G3 MCTS path",
        reasoning="gauntlet forced MCTS recovery",
        pass_rate=1.0,
        rollouts_used=4,
        elapsed_sec=0.05,
    )

    with patch.object(orch.worker, "execute_task", side_effect=fail_worker), patch(
        "scaffold.agent.orchestrator.MCTSSearchEngine"
    ) as mock_engine_cls, patch.object(
        orch.decomposer, "decompose", return_value=[]
    ), patch.object(orch, "_cheap_call", return_value=""), patch(
        "scaffold.agent.core.performance_tracker.ToolPerformanceTracker._load"
    ):
        mock_engine_cls.return_value.search.return_value = mock_mcts_result
        run = orch.execute_feature(
            goal="G3 failure hunt — force MCTS after worker fail",
            codebase_root=str(ROOT),
            pre_planned_tasks=plan,
        )

    mcts_after = _mcts_trace_count()
    patterns_after = _error_pattern_count()
    last_pattern = _last_error_pattern()
    typed_ok = bool(
        last_pattern
        and last_pattern.get("error_type")
        and last_pattern.get("error_type") != "UNKNOWN"
        and patterns_after > patterns_before
    )
    return {
        "scenario": "G3",
        "mcts_before": mcts_before,
        "mcts_after": mcts_after,
        "patterns_before": patterns_before,
        "patterns_after": patterns_after,
        "last_error_type": (last_pattern or {}).get("error_type"),
        "typed_error_ok": typed_ok,
        "run_success": run.get("success"),
        "pass": (
            mcts_after > mcts_before
            and run.get("success") is True
            and typed_ok
        ),
    }


def run_g2_pause_resume() -> dict[str, Any]:
    """G2: 3-task plan, pause after task 1, resume completes 2-3."""
    os.chdir(ROOT)
    env = _apply_defaults({"AWOS_E2E": "1", "AWOS_MCTS_DISABLE": "true"})
    if not env.get("ANTHROPIC_API_KEY"):
        env["ANTHROPIC_API_KEY"] = "fake-key-for-gauntlet"
    for k, v in env.items():
        os.environ[k] = v

    from unittest.mock import patch

    from scaffold.agent.orchestrator import Orchestrator

    repo = ROOT
    goal = "Gauntlet G2 pause-mid-plan proof"
    plan = [
        {
            "task_id": 1,
            "task_type": "edit_file",
            "path": "tests/fixtures/gauntlet_marker_a.py",
            "file": "tests/fixtures/gauntlet_marker_a.py",
            "action": "Ensure marker A",
            "complexity": "low",
        },
        {
            "task_id": 2,
            "task_type": "edit_file",
            "path": "tests/fixtures/gauntlet_marker_b.py",
            "file": "tests/fixtures/gauntlet_marker_b.py",
            "action": "Ensure marker B",
            "complexity": "low",
        },
        {
            "task_id": 3,
            "task_type": "edit_file",
            "path": "tests/fixtures/gauntlet_marker_c.py",
            "file": "tests/fixtures/gauntlet_marker_c.py",
            "action": "Ensure marker C",
            "complexity": "low",
        },
    ]
    for name in ("gauntlet_marker_a.py", "gauntlet_marker_b.py", "gauntlet_marker_c.py"):
        p = repo / "tests" / "fixtures" / name
        if not p.exists():
            p.write_text(f"# {name}\n", encoding="utf-8")

    orch = Orchestrator()
    executed: list[Any] = []

    def mock_execute(task, ctx):
        executed.append(task["task_id"])
        if len(executed) == 1:
            orch._pause_requested = True
        return {"task_id": task["task_id"], "success": True, "task": task}

    with patch.object(orch, "_execute_single_task", side_effect=mock_execute), patch(
        "scaffold.agent.core.performance_tracker.ToolPerformanceTracker._load"
    ):
        first = orch.execute_feature(
            goal=goal,
            codebase_root=str(repo),
            pre_planned_tasks=plan,
        )

    rs_id = first.get("runtime_session_id")
    from scaffold.agent.runtime_session import RuntimeSessionStore, SessionStatus

    store = RuntimeSessionStore()
    paused = store.load(rs_id)
    result = {
        "scenario": "G2",
        "session_id": rs_id,
        "phase1_executed": executed,
        "status_after_pause": paused.status.value,
        "completed_after_pause": list(paused.progress.completed_task_ids),
    }

    if paused.status != SessionStatus.PAUSED:
        result["pass"] = False
        result["error"] = f"expected PAUSED, got {paused.status.value}"
        return result

    executed.clear()
    orch2 = Orchestrator()

    def mock_execute_resume(task, ctx):
        executed.append(task["task_id"])
        return {"task_id": task["task_id"], "success": True, "task": task}

    with patch.object(orch2, "_execute_single_task", side_effect=mock_execute_resume), patch(
        "scaffold.agent.core.performance_tracker.ToolPerformanceTracker._load"
    ):
        second = orch2.execute_feature(
            goal=goal,
            codebase_root=str(repo),
            pre_planned_tasks=plan,
            session_id=rs_id,
            resume=True,
        )

    done = store.load(rs_id)
    result.update(
        {
            "phase2_executed": executed,
            "status_final": done.status.value,
            "completed_final": list(done.progress.completed_task_ids),
            "pass": (
                done.status == SessionStatus.COMPLETED
                and executed == [2, 3]
                and done.progress.completed_task_ids == [1, 2, 3]
                and second.get("success") is True
            ),
        }
    )
    return result


def run_awos_scenario(scenario: dict[str, Any], *, timeout_s: int = 300) -> dict[str, Any]:
    """Run awos_run type scenario via subprocess."""
    if scenario["id"] in ("G3L", "G3"):
        _reset_gauntlet_broken_module()
    env = _apply_defaults(scenario.get("env"))
    goal = scenario["goal"]
    mcts_before = _mcts_trace_count()

    cmd = [sys.executable, "-u", str(ROOT / "awos.py"), "run", goal]
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    lines: list[str] = []
    session_id: Optional[str] = None
    deadline = time.time() + timeout_s
    try:
        assert proc.stdout is not None
        while True:
            if time.time() > deadline:
                proc.kill()
                break
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            if line:
                lines.append(line)
                print(line, end="")
                if "[SESSION] Runtime session rs_" in line:
                    for tok in line.split():
                        if tok.startswith("rs_"):
                            session_id = tok.strip("()")
                            break
    finally:
        if proc.poll() is None:
            proc.wait(timeout=30)

    out = "".join(lines)
    mcts_after = _mcts_trace_count()
    pass_checks = scenario.get("pass", [])
    checks = {c: c in out for c in pass_checks}
    if "mcts_traces.jsonl" in pass_checks:
        checks["mcts_traces.jsonl"] = mcts_after > mcts_before

    return {
        "scenario": scenario["id"],
        "session_id": session_id,
        "exit_code": proc.returncode,
        "mcts_before": mcts_before,
        "mcts_after": mcts_after,
        "checks": checks,
        "pass": all(checks.values()) if checks else proc.returncode == 0,
    }


def _prep_g6_markers() -> None:
    for name in ("gauntlet_marker_a.py", "gauntlet_marker_b.py", "gauntlet_marker_c.py"):
        p = ROOT / "tests" / "fixtures" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_text(f"# {name}\n", encoding="utf-8")


def _parse_session_id(line: str) -> Optional[str]:
    if "Runtime session rs_" not in line:
        return None
    for part in line.split():
        if part.startswith("rs_"):
            return part.strip("()")
    return None


def run_g6_sigint_pause(
    goal: str, *, timeout_s: int = 600, extra_env: Optional[dict[str, str]] = None
) -> dict[str, Any]:
    """G6: live SIGINT after task 1 → paused session → awos sessions resume."""
    _prep_g6_markers()
    base = {"AWOS_E2E": "1", "AWOS_LEARNING_DISABLE": "true"}
    if extra_env:
        base.update(extra_env)
    env = _apply_defaults(base)

    cmd = [sys.executable, "-u", str(ROOT / "awos.py"), "run", goal]
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    lines: list[str] = []
    session_id: Optional[str] = None
    sent_sigint = False
    task1_done = False
    deadline = time.time() + timeout_s

    assert proc.stdout is not None
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line and proc.poll() is not None:
            break
        if not line:
            continue
        lines.append(line)
        print(line, end="")
        sid = _parse_session_id(line)
        if sid:
            session_id = sid
        if "[TASK 1]" in line and ("VERIFIED" in line or "task complete" in line.lower()):
            task1_done = True
        if "[SESSION] Pause requested" in line:
            break
        if task1_done and not sent_sigint and "task complete" in line.lower():
            time.sleep(0.3)
            proc.send_signal(signal.SIGINT)
            sent_sigint = True
    try:
        proc.wait(timeout=120)
    except subprocess.TimeoutExpired:
        proc.kill()

    from scaffold.agent.runtime_session import RuntimeSessionStore, SessionStatus

    store = RuntimeSessionStore()
    paused_ok = False
    completed_after_pause: list[Any] = []
    if session_id:
        try:
            paused = store.load(session_id)
            paused_ok = paused.status == SessionStatus.PAUSED
            completed_after_pause = list(paused.progress.completed_task_ids)
        except (FileNotFoundError, ValueError):
            pass

    resume_ok = False
    final_status = None
    final_completed: list[Any] = []
    if session_id and paused_ok:
        print(f"\n[G6] Resuming session {session_id}…\n")
        resume_cmd = [sys.executable, "-u", str(ROOT / "awos.py"), "sessions", "resume", session_id]
        resume_proc = subprocess.run(
            resume_cmd,
            cwd=str(ROOT),
            env=env,
            capture_output=False,
            timeout=timeout_s,
        )
        resume_ok = resume_proc.returncode == 0
        try:
            done = store.load(session_id)
            final_status = done.status.value
            final_completed = list(done.progress.completed_task_ids)
            resume_ok = resume_ok and done.status == SessionStatus.COMPLETED
        except (FileNotFoundError, ValueError):
            resume_ok = False

    return {
        "scenario": "G6",
        "session_id": session_id,
        "sigint_sent": sent_sigint,
        "paused_ok": paused_ok,
        "completed_after_pause": completed_after_pause,
        "resume_ok": resume_ok,
        "status_final": final_status,
        "completed_final": final_completed,
        "pass": bool(
            session_id
            and sent_sigint
            and paused_ok
            and 1 in completed_after_pause
            and resume_ok
            and len(final_completed) >= 3
        ),
    }


def run_g6_from_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    return run_g6_sigint_pause(scenario.get("goal", ""), extra_env=scenario.get("env"))


def run_g7_verify_replan() -> dict[str, Any]:
    """G7: verify-fail → replan → success on revised task."""
    import tempfile
    from unittest.mock import patch

    from scaffold.agent.orchestrator import Orchestrator

    env = _apply_defaults({"AWOS_E2E": "1", "AWOS_MCTS_DISABLE": "true"})
    if not env.get("ANTHROPIC_API_KEY"):
        env["ANTHROPIC_API_KEY"] = "fake-key-for-gauntlet"
    for k, v in env.items():
        os.environ[k] = v

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        py = root / "worker.py"
        py.write_text(
            "class W:\n"
            "    def _try_cheap_fallback(self, x):\n"
            "        return x\n",
            encoding="utf-8",
        )

        bad_search = "    def _try_cheap_fallback(self, x):\n        return x\n"
        bad_replace = (
            '    def _try_cheap_fallback(self, x):\n'
            '        """Uses alternate cheap providers."""\n'
            "        return x\n"
        )
        good_search = "    def _try_cheap_fallback(self, x):\n"
        good_replace = (
            "    # Tries alternate cheap providers when primary fails.\n"
            "    def _try_cheap_fallback(self, x):\n"
        )
        calls = {"n": 0}

        def mock_worker(task, file_content, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return {
                    "success": True,
                    "search": bad_search,
                    "replace": bad_replace,
                    "reasoning": "bad docstring attempt",
                }
            return {
                "success": True,
                "search": good_search,
                "replace": good_replace,
                "reasoning": "comment above function",
            }

        plan = [
            {
                "task_id": 1,
                "task_type": "edit_file",
                "path": "worker.py",
                "file": "worker.py",
                "action": "Add comment above `_try_cheap_fallback` explaining cheap providers",
                "complexity": "low",
            }
        ]

        replan_seen = {"v": False}
        orig_replan = Orchestrator._replan_after_verify_fail

        def _track_replan(self, failed_task, verify_error, codebase_context):
            replan_seen["v"] = True
            return orig_replan(self, failed_task, verify_error, codebase_context)

        orch = Orchestrator()
        with patch.object(orch.worker, "execute_task", side_effect=mock_worker), patch.object(
            Orchestrator, "_replan_after_verify_fail", _track_replan
        ), patch("scaffold.agent.core.performance_tracker.ToolPerformanceTracker._load"), patch.object(
            orch.post_mortem, "reflect", return_value=None
        ), patch.object(orch, "_cheap_call", return_value=""):
            run = orch.execute_feature(
                goal="G7 verify-fail replan proof",
                codebase_root=str(root),
                pre_planned_tasks=plan,
            )

        text = py.read_text(encoding="utf-8")
        return {
            "scenario": "G7",
            "run_success": run.get("success"),
            "replan_seen": replan_seen["v"],
            "worker_calls": calls["n"],
            "comment_applied": "# Tries alternate cheap providers" in text,
            "pass": bool(
                run.get("success")
                and replan_seen["v"]
                and calls["n"] == 2
                and "# Tries alternate cheap providers" in text
            ),
        }


def run_scenario(scenario_id: str) -> dict[str, Any]:
    suite = _load_suite()
    scenario = next((s for s in suite["scenarios"] if s["id"] == scenario_id), None)
    if not scenario:
        raise SystemExit(f"Unknown scenario: {scenario_id}")

    stype = scenario.get("type")
    if stype == "orchestrator_pause_resume":
        return run_g2_pause_resume()
    if stype == "orchestrator_mcts_failure":
        return run_g3_mcts_failure()
    if stype == "orchestrator_verify_replan":
        return run_g7_verify_replan()
    if stype == "g6_sigint_live":
        return run_g6_from_scenario(scenario)
    if stype == "awos_run":
        return run_awos_scenario(scenario)
    raise SystemExit(f"Unsupported scenario type: {stype}")


def run_all() -> list[dict[str, Any]]:
    suite = _load_suite()
    results = []
    for s in suite["scenarios"]:
        if s.get("manual_review"):
            print(f"[skip] {s['id']} manual review — see booklet")
            continue
        print(f"\n{'='*60}\n  GAUNTLET {s['id']}: {s['name']}\n{'='*60}\n")
        try:
            results.append(run_scenario(s["id"]))
        except Exception as exc:
            results.append({"scenario": s["id"], "pass": False, "error": str(exc)})
    return results


def main() -> None:
    if len(sys.argv) < 2:
        list_scenarios()
        return
    cmd = sys.argv[1]
    if cmd == "list":
        list_scenarios()
    elif cmd == "run" and len(sys.argv) >= 3:
        r = run_scenario(sys.argv[2].upper())
        print("\n┌─ GAUNTLET RESULT ───────────────────────────────────────")
        print(json.dumps(r, indent=2))
        print(f"│  PASS: {r.get('pass', False)}")
        print("└──────────────────────────────────────────────────────────\n")
        raise SystemExit(0 if r.get("pass") else 1)
    elif cmd == "run-all":
        results = run_all()
        passed = sum(1 for r in results if r.get("pass"))
        print(f"\nGauntlet batch: {passed}/{len(results)} passed")
        raise SystemExit(0 if passed == len(results) else 1)
    else:
        print("Usage: stage1_gauntlet_runner.py list|run <ID>|run-all")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
