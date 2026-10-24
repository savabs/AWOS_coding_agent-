"""
Engine early-stop fixes from the ladder H1 (Haiku) result, where AWOS lost
7 tasks to mini-swe-agent by stopping early
(docs/memory/runs/ladder_h1_haiku_2026-10-15.md):

F1  AWOS_FULL_SUITE_GATE   a green verdict from name-mapped tests is confirmed
                           by one full visible-suite run (astroid_3286: 17
                           mapped passed, 61 other visible tests broken).
F2  AWOS_VERIFY_UNCHECKED  a visible-green one-shot with an inactive acceptance
                           gate gets a bounded verify loop (click_3818,
                           markdown-it-py_433 shipped incomplete).
F3  AWOS_REPAIR_EXTEND     the bounded acceptance repair gets extra rounds while
                           it makes progress (sqlglot_8574: 0 -> 2 of 4 passing
                           when the 10-turn cap ran out).
+   AWOS_ACCEPTANCE_START_TIMEOUT  the acceptance start run's own cap.

No network: fake clients, scripted agent models, mocked test runs.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from test_acceptance import FIX, GREEN, _orchestrator, _project  # noqa: E402
from test_acceptance_v2 import (  # noqa: E402
    FAIL2, FAIL_B, GREEN_CALC, PASS, SUITE_SRC, _LoopSpy, _SeqClient, _SideEffectScripted,
)

from scaffold.agent import acceptance  # noqa: E402
from scaffold.agent.acceptance import AcceptanceSuite  # noqa: E402
from scaffold.agent.agent_loop import ModelReply, ToolCall  # noqa: E402
from scaffold.agent.test_runner import TestResult, TestRunner, workspace_tree_hash  # noqa: E402

MAPPED_GREEN = TestResult(passed=17, failed=0, errors=0, pass_rate=1.0,
                          raw_output="17 passed", no_tests_found=False, mode="mapped")
RED = TestResult(passed=2, failed=1, errors=0, pass_rate=0.66, raw_output="1 failed",
                 no_tests_found=False)
ARB_BOTH = '{"test_a": "CODE_INCOMPLETE", "test_b": "CODE_INCOMPLETE"}'


def _full_red():
    return TestResult(passed=100, failed=61, errors=0, pass_rate=0.62, no_tests_found=False,
                      raw_output="FAILED tests/test_raw.py::test_other - AssertionError",
                      mode="full")


def _full_green():
    return TestResult(passed=161, failed=0, errors=0, pass_rate=1.0, no_tests_found=False,
                      raw_output="161 passed", mode="full")


def _full_timeout():
    return TestResult(timed_out=True, no_tests_found=False, mode="full",
                      raw_output="timed out after 180s")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("AWOS_SAFE_TO_RUN_TESTS", "1")
    for name in ("AWOS_FULL_SUITE_GATE", "AWOS_VERIFY_UNCHECKED", "AWOS_VERIFY_TURNS",
                 "AWOS_REPAIR_EXTEND", "AWOS_REPAIR_EXTRA_ROUNDS", "AWOS_FULL_SUITE_MARKER",
                 "AWOS_ACCEPTANCE_START_TIMEOUT", "AWOS_ACCEPTANCE_FIRST",
                 "AWOS_ACCEPTANCE_EARLY_STOP", "AWOS_MAX_RUN_COST", "AWOS_GOAL_BUDGET_USD"):
        monkeypatch.delenv(name, raising=False)


def _git_project():
    root = _project()
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    return root


def _run(*, suite=None, checks=(), arb_reply="{}", runs=(), fulls=(), script=None,
         one_shot="1", mode="2", turns="3", retries="0", env=None, root=None, marker=None):
    """One task through _execute_task_via_agent_loop with everything faked."""
    root = root or _project()
    orch = _orchestrator()
    orch._run_files_changed = set()
    ctx = {"codebase_root": str(root), "git": MagicMock(),
           "session": MagicMock(goal="fix add so add(1, 2) == 3"),
           "codebase_context": {}, "exploration": {}}
    spec = SimpleNamespace(model_id="claude-haiku-4-5", name="Haiku", cost_per_req=0.01,
                           level=SimpleNamespace(value=3))
    run_results = list(runs)
    full_results = list(fulls)
    runner = MagicMock()
    runner.return_value.run.side_effect = (
        lambda **kw: run_results.pop(0) if run_results else GREEN)
    runner.return_value.run_full_suite.side_effect = (
        lambda: full_results.pop(0) if full_results else _full_green())
    runner.return_value.full_suite_marker_green.return_value = marker
    scripted = _SideEffectScripted(script or [ModelReply(text="Done.")])
    if suite is None:
        suite = AcceptanceSuite(source=SUITE_SRC, generated=2, kept=["test_a", "test_b"],
                                cost_usd=0.001)
    check = MagicMock(side_effect=list(checks))
    seq = _SeqClient(([FIX] if one_shot == "1" else []) + [arb_reply])
    _LoopSpy.inits = []
    with patch.dict(os.environ, {"AWOS_ONE_SHOT": one_shot, "AWOS_ACCEPTANCE": mode,
                                 "AWOS_AGENT_RETRIES": retries,
                                 "AWOS_ACCEPTANCE_REPAIR_TURNS": turns, **(env or {})}), \
         patch("scaffold.agent.agent_loop.build_client_from_env", return_value=scripted), \
         patch("scaffold.agent.agent_loop.AgentLoop", _LoopSpy), \
         patch("scaffold.agent.one_shot.one_shot_client", return_value=seq), \
         patch("scaffold.agent.acceptance.build_suite", return_value=suite), \
         patch("scaffold.agent.acceptance.run_acceptance_detail", check), \
         patch("scaffold.agent.orchestrator.TestRunner", runner):
        out = orch._execute_task_via_agent_loop(
            {"task_id": 1, "action": "fix add in pkg/calc.py", "file": "pkg/calc.py"}, ctx,
            task_id=1, esc_decision=SimpleNamespace(spec=spec), _span=MagicMock(),
            _strategy=SimpleNamespace(name="default"), _task_ts=0.0, _live_t=None,
            _task_usage={"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
        )
    return SimpleNamespace(out=out, orch=orch, root=root, runner=runner, check=check,
                           scripted=scripted, inits=list(_LoopSpy.inits))


def _inactive(error="start run timed out (240s cap)"):
    return AcceptanceSuite(generated=0, kept=[], error=error, cost_usd=0.001)


def _edit(old, new, tid="e1"):
    return ModelReply(text="", tool_calls=[ToolCall(id=tid, name="edit_file", arguments={
        "path": "pkg/calc.py", "old_string": old, "new_string": new})],
        input_tokens=100, output_tokens=20)


# ── F1: full-suite success gate ─────────────────────────────────────────────

def test_f1_mapped_green_full_red_one_shot_falls_back(capsys):
    r = _run(suite=_inactive(), mode="0", runs=[MAPPED_GREEN, MAPPED_GREEN],
             fulls=[_full_red()])
    log = capsys.readouterr().out
    assert "[GATE] full suite: 17 mapped test(s) passed; running the full visible suite" in log
    assert "[GATE] full suite red: 61 new failure(s)" in log
    assert "[ONE-SHOT] fell back to the agent loop: tests: 100 passed, 61 failed" in log
    # The agent loop got the failing full-suite output as feedback.
    assert "NEW failure(s) vs the base commit" in r.scripted.prompts[0]
    assert "test_raw.py::test_other" in r.scripted.prompts[0]
    # Same tree after the do-nothing loop: the full suite is not run again.
    assert r.runner.return_value.run_full_suite.call_count == 1
    assert "[GATE] full suite already ran on this tree" in log
    assert r.out["success"] is False
    assert r.orch._components["full_suite_gate"]["red"] >= 1


def test_f1_full_timeout_keeps_the_mapped_verdict(capsys):
    r = _run(suite=_inactive(), mode="0", runs=[MAPPED_GREEN], fulls=[_full_timeout()])
    log = capsys.readouterr().out
    assert "[GATE] full suite timed out; keeping the mapped verdict (17 mapped passed)" in log
    assert r.out["success"] is True and r.out.get("one_shot") is True
    assert r.inits == []                       # no agent loop


def test_f1_full_green_is_the_reported_result(capsys):
    r = _run(suite=_inactive(), mode="0", runs=[MAPPED_GREEN], fulls=[_full_green()])
    log = capsys.readouterr().out
    assert "[GATE] full suite green: 161 passed" in log
    assert r.out["success"] is True
    assert "[TASK 1] Tests: 161 passed, 0 failed" in log


def test_f1_agent_loop_attempt_resumes_on_full_red(capsys):
    script = [_edit("return a - b", "return a + b"), ModelReply(text="Fixed."),
              ModelReply(text="Still fixed.")]
    r = _run(one_shot="0", mode="0", retries="1", script=script,
             runs=[MAPPED_GREEN, MAPPED_GREEN], fulls=[_full_red()])
    log = capsys.readouterr().out
    assert "AgentLoop resuming after: 61 test(s) failed after its edits" in log
    assert r.runner.return_value.run_full_suite.call_count == 1   # same tree, once
    assert r.out["success"] is False


def test_f1_switch_off_is_the_old_behaviour(capsys):
    r = _run(suite=_inactive(), mode="0", runs=[MAPPED_GREEN], fulls=[_full_red()],
             env={"AWOS_FULL_SUITE_GATE": "0"})
    assert "[GATE]" not in capsys.readouterr().out
    assert r.runner.return_value.run_full_suite.call_count == 0
    assert r.out["success"] is True


def test_f1_gate_runs_once_per_tree_and_again_on_a_new_tree():
    root = _project()
    orch = _orchestrator()
    orch._full_gate_cache = {}
    runner = MagicMock()
    runner.return_value.full_suite_marker_green.return_value = None
    runner.return_value.run_full_suite.side_effect = lambda: _full_green()
    with patch("scaffold.agent.orchestrator.TestRunner", runner):
        a = orch._full_suite_gate(MAPPED_GREEN, str(root), None, 1)
        b = orch._full_suite_gate(MAPPED_GREEN, str(root), None, 1)
        assert runner.return_value.run_full_suite.call_count == 1
        assert a is b and a.mode == "full"
        (root / "pkg" / "calc.py").write_text(GREEN_CALC, encoding="utf-8")
        orch._full_suite_gate(MAPPED_GREEN, str(root), None, 1)
        assert runner.return_value.run_full_suite.call_count == 2


def test_f1_judges_own_full_run_counts_for_the_tree():
    root = _project()
    orch = _orchestrator()
    orch._full_gate_cache = {}
    runner = MagicMock()
    with patch("scaffold.agent.orchestrator.TestRunner", runner):
        full = _full_green()
        assert orch._full_suite_gate(full, str(root), None, 1) is full
        assert orch._full_suite_gate(MAPPED_GREEN, str(root), None, 1) is full
    runner.return_value.run_full_suite.assert_not_called()


def test_f1_red_or_unmapped_results_never_trigger_the_full_suite():
    orch = _orchestrator()
    runner = MagicMock()
    with patch("scaffold.agent.orchestrator.TestRunner", runner):
        red = TestResult(passed=3, failed=1, no_tests_found=False, mode="mapped")
        assert orch._full_suite_gate(red, str(_project()), None, 1) is red
        assert orch._full_suite_gate(GREEN, str(_project()), None, 1) is GREEN   # mode ""
        assert orch._full_suite_gate(None, str(_project()), None, 1) is None
    runner.return_value.run_full_suite.assert_not_called()


def test_f1_green_marker_for_the_tree_is_reused(tmp_path, monkeypatch, capsys):
    root = _project()
    marker = tmp_path / "full_suite_green.json"
    monkeypatch.setenv("AWOS_FULL_SUITE_MARKER", str(marker))
    marker.write_text(json.dumps({
        "tree": workspace_tree_hash(str(root)),
        "command": TestRunner(project_root=str(root)).full_suite_key(), "passed": 9,
        "failed": 0, "errors": 0}), encoding="utf-8")
    orch = _orchestrator()
    orch._full_gate_cache = {}
    with patch.object(TestRunner, "run_full_suite", side_effect=AssertionError("ran")) as rf:
        assert orch._full_suite_gate(MAPPED_GREEN, str(root), None, 1) is MAPPED_GREEN
    rf.assert_not_called()
    assert "full-suite marker records a green run on this tree (9 passed)" in \
        capsys.readouterr().out
    # A different tree: the marker does not apply.
    (root / "pkg" / "calc.py").write_text(GREEN_CALC, encoding="utf-8")
    assert TestRunner(project_root=str(root)).full_suite_marker_green() is None


def test_f1_run_full_suite_records_the_marker_for_the_host(tmp_path, monkeypatch):
    root = _project()
    marker = tmp_path / "m.json"
    monkeypatch.setenv("AWOS_FULL_SUITE_MARKER", str(marker))
    green = TestResult(passed=4, failed=0, errors=0, no_tests_found=False)
    with patch.object(TestRunner, "_execute", return_value=green):
        res = TestRunner(project_root=str(root)).run_full_suite()
    assert res.mode == "full" and res.passed == 4
    data = json.loads(marker.read_text())
    assert data["tree"] == workspace_tree_hash(str(root))
    assert TestRunner(project_root=str(root)).full_suite_marker_green() is not None


# ── F2: verify an unchecked one-shot ────────────────────────────────────────

def test_f2_inactive_gate_runs_the_verify_loop(capsys):
    root = _git_project()
    r = _run(suite=_inactive(), root=root, env={"AWOS_VERIFY_UNCHECKED": "1"},
             script=[ModelReply(text="Every requirement verified.")])
    log = capsys.readouterr().out
    assert ("[VERIFY] acceptance gate inactive (start run timed out (240s cap)); the "
            "one-shot edit is visible-green but unchecked against the issue: verify loop, "
            "at most 15 turns") in log
    assert "[VERIFY] done (completed, 1 turn(s)): no change needed" in log
    assert r.inits == [15]
    prompt = r.scripted.prompts[0]
    assert "fix add so add(1, 2) == 3" in prompt                # the goal, verbatim
    assert "+    return a + b" in prompt                        # the current diff
    assert "reply with a short summary and NO tool calls" in prompt
    assert r.out["success"] is True and r.out.get("one_shot") is True
    assert r.orch._components["verify"]["status"] == "ran"


def test_f2_active_gate_runs_no_verify_loop(capsys):
    r = _run(checks=[PASS], env={"AWOS_VERIFY_UNCHECKED": "1"})
    assert "[VERIFY]" not in capsys.readouterr().out
    assert r.inits == []
    assert r.out["success"] is True


def test_f2_acceptance_off_also_counts_as_unchecked(capsys):
    r = _run(mode="0", env={"AWOS_VERIFY_UNCHECKED": "1", "AWOS_VERIFY_TURNS": "4"})
    assert "[VERIFY] acceptance gate inactive (acceptance off)" in capsys.readouterr().out
    assert r.inits == [4]


def test_f2_verify_fix_is_kept_when_green(capsys):
    script = [_edit("def mul(a, b):", "def sub(a, b):\n    return a - b\n\n\ndef mul(a, b):"),
              ModelReply(text="Added the missing sub; all verified.")]
    r = _run(suite=_inactive(), env={"AWOS_VERIFY_UNCHECKED": "1"}, script=script,
             runs=[GREEN, GREEN])
    log = capsys.readouterr().out
    assert "[VERIFY] done (completed, 2 turn(s)): changed pkg/calc.py" in log
    assert "def sub(a, b)" in (r.root / "pkg" / "calc.py").read_text()
    assert r.out["success"] is True


def test_f2_verify_that_breaks_tests_restores_the_one_shot(capsys):
    script = [_edit("return a * b", "return a ** b"), ModelReply(text="Done.")]
    r = _run(suite=_inactive(), env={"AWOS_VERIFY_UNCHECKED": "1"}, script=script,
             runs=[GREEN, RED])
    log = capsys.readouterr().out
    assert "[VERIFY] the verify loop left the visible tests red (2 passed, 1 failed); " \
           "restored the green one-shot edit" in log
    assert (r.root / "pkg" / "calc.py").read_text() == GREEN_CALC   # one-shot state back
    assert r.out["success"] is True and "[TASK 1] Tests: 3 passed, 0 failed" in log


def test_f2_switch_off_is_the_old_behaviour(capsys):
    r = _run(suite=_inactive(), env={"AWOS_VERIFY_UNCHECKED": "0"})
    assert "[VERIFY]" not in capsys.readouterr().out
    assert r.inits == []
    assert r.out["success"] is True


def test_f2_skipped_when_the_cost_cap_is_spent(capsys):
    r = _run(suite=_inactive(), env={"AWOS_VERIFY_UNCHECKED": "1",
                                     "AWOS_MAX_RUN_COST": "0.0000001"})
    assert "[VERIFY] skipped:" in capsys.readouterr().out
    assert r.inits == []


def test_f2_default_is_on_outside_pytest(monkeypatch):
    from scaffold.agent.orchestrator import _verify_unchecked_enabled
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert _verify_unchecked_enabled() is True
    monkeypatch.setenv("AWOS_VERIFY_UNCHECKED", "0")
    assert _verify_unchecked_enabled() is False


# ── F3: keep repairing while acceptance still fails ─────────────────────────

_ROUND1 = [_edit("def mul(a, b):", "def sub(a, b):\n    return a - b\n\n\ndef mul(a, b):"),
           ModelReply(text="Round one done.")]
_ROUND2_EDIT = _edit("def mul(a, b):", "def div(a, b):\n    return a / b\n\n\ndef mul(a, b):",
                     tid="e2")


def test_f3_progress_earns_an_extra_round_that_stops_when_all_pass(capsys):
    # Checks: one-shot (first), round 1 done check, round 1 post check
    # (0 -> 1 of 2: progress), round 2 done check (all pass: early stop).
    r = _run(checks=[FAIL2, FAIL_B, FAIL_B, PASS], arb_reply=ARB_BOTH,
             script=[*_ROUND1, _ROUND2_EDIT, ModelReply(text="never reached")])
    log = capsys.readouterr().out
    assert "[ACCEPTANCE] extra repair round 1: 1 of 2 kept acceptance test(s) pass " \
           "(up from 0); at most 3 more turns" in log
    assert "repair stopped early after turn 1 of 3" in log
    assert "[ACCEPTANCE] repair passed (acceptance_green, 3 turn(s), 2 rounds)" in log
    assert r.inits == [3, 3]
    assert r.check.call_count == 4
    text = (r.root / "pkg" / "calc.py").read_text()
    assert "def sub" in text and "def div" in text            # repaired state kept
    assert r.out["success"] is True
    assert r.orch._components["repair_extend"]["rounds"] == 1
    # The extra round's prompt says where the earlier round got to.
    assert any("got 1 of 2 acceptance tests passing" in p for p in r.scripted.prompts)


def test_f3_no_progress_stops_and_restores(capsys):
    r = _run(checks=[FAIL2, FAIL2, FAIL2], arb_reply=ARB_BOTH, script=list(_ROUND1))
    log = capsys.readouterr().out
    assert "[ACCEPTANCE] no extra repair round: round 1 made no progress " \
           "(0 of 2 kept passing, 0 before it)" in log
    assert r.inits == [3]
    assert "[ACCEPTANCE] repair failed; restored the green edit" in log
    assert (r.root / "pkg" / "calc.py").read_text() == GREEN_CALC


def test_f3_second_round_without_progress_stops(capsys):
    r = _run(checks=[FAIL2, FAIL_B, FAIL_B, FAIL_B, FAIL_B], arb_reply=ARB_BOTH,
             script=[*_ROUND1, _ROUND2_EDIT, ModelReply(text="Round two done.")])
    log = capsys.readouterr().out
    assert "extra repair round 1:" in log
    assert "no extra repair round: round 2 made no progress (1 of 2 kept passing, " \
           "1 before it)" in log
    assert r.inits == [3, 3]
    assert "2 rounds;" in log and (r.root / "pkg" / "calc.py").read_text() == GREEN_CALC


def test_f3_switch_off_is_one_round(capsys):
    r = _run(checks=[FAIL2, FAIL_B, FAIL_B], arb_reply=ARB_BOTH, script=list(_ROUND1),
             env={"AWOS_REPAIR_EXTEND": "0"})
    assert "extra repair round" not in capsys.readouterr().out
    assert r.inits == [3]


def test_f3_extra_rounds_knob():
    with patch.dict(os.environ, {"AWOS_REPAIR_EXTRA_ROUNDS": "5"}):
        assert acceptance.repair_extra_rounds() == 5
    with patch.dict(os.environ, {"AWOS_REPAIR_EXTRA_ROUNDS": "x"}):
        assert acceptance.repair_extra_rounds() == 2
    with patch.dict(os.environ, {"AWOS_REPAIR_EXTEND": "0", "AWOS_REPAIR_EXTRA_ROUNDS": "5"}):
        assert acceptance.repair_extra_rounds() == 0
    assert acceptance.repair_extra_rounds() == 2


def _decide(orch, **kw):
    args = dict(round_no=1, extra_left=2, k=4, passing_before=0,
                failing_now={"t3": "x", "t4": "y"},
                outcome=SimpleNamespace(stop_reason="max_turns", cost_usd=0.01, turns=10),
                cost_cap=None, spent=0.0)
    args.update(kw)
    return orch._repair_extend_decision(**args)


def test_f3_decision_budget_and_limits(capsys):
    orch = _orchestrator()
    orch._goal_budget_exhausted = lambda: False
    assert _decide(orch) == 2                                       # progress: go on
    assert _decide(orch, extra_left=0) is None                      # rounds used up
    assert _decide(orch, failing_now=None) is None                  # no evidence
    assert _decide(orch, cost_cap=0.05, spent=0.05) is None         # cap spent
    assert "no extra repair round: $0.0500 of the $0.0500 cap spent" in capsys.readouterr().out
    assert _decide(orch, cost_cap=1.0, spent=0.05) == 2
    assert _decide(orch, outcome=SimpleNamespace(stop_reason="cost_cap", cost_usd=0.0,
                                                 turns=1)) is None
    orch._goal_budget_exhausted = lambda: True                      # goal budget gone
    assert _decide(orch) is None
    orch._goal_budget_exhausted = lambda: False
    orch._task_t0 = time.monotonic() - 10_000                       # wall clock gone
    assert _decide(orch) is None
    assert "of the task's wall clock left" in capsys.readouterr().out


def test_f3_budget_exhausted_stops_after_the_first_round(capsys):
    with patch("scaffold.agent.orchestrator.Orchestrator._goal_budget_exhausted",
               side_effect=[False, True]):
        r = _run(checks=[FAIL2, FAIL_B, FAIL_B], arb_reply=ARB_BOTH, script=list(_ROUND1))
    log = capsys.readouterr().out
    assert "[ACCEPTANCE] no extra repair round: " in log and "goal budget" in log
    assert r.inits == [3]


# ── acceptance start-run timeout knob ───────────────────────────────────────

def test_start_timeout_knob(monkeypatch):
    assert acceptance.start_timeout_sec() == 240
    monkeypatch.setenv("AWOS_ACCEPTANCE_START_TIMEOUT", "600")
    assert acceptance.start_timeout_sec() == 600
    monkeypatch.setenv("AWOS_ACCEPTANCE_START_TIMEOUT", "soon")
    assert acceptance.start_timeout_sec() == 240


def test_start_run_uses_its_own_timeout_and_checks_keep_theirs(monkeypatch):
    root = _project()
    seen = []

    def _exec(self, cfg):
        seen.append(cfg.timeout_sec)
        return TestResult(raw_output="", no_tests_found=True)

    monkeypatch.setenv("AWOS_ACCEPTANCE_START_TIMEOUT", "300")
    gen = {"source": SUITE_SRC, "error": "", "cost_usd": 0.0, "input_tokens": 0,
           "output_tokens": 0}
    with patch.object(TestRunner, "_execute", _exec), \
         patch.object(acceptance, "generate_acceptance_tests", return_value=gen):
        acceptance.build_suite("goal", str(root), client=None, model="m")
        acceptance.run_tests_once(str(root), SUITE_SRC)
    assert seen == [300, acceptance.RUN_TIMEOUT_SEC]


def test_start_run_timeout_leaves_the_gate_inactive_so_verify_applies():
    root = _project()
    gen = {"source": SUITE_SRC, "error": "", "cost_usd": 0.0, "input_tokens": 0,
           "output_tokens": 0}
    timed_out = {"outcomes": {}, "collection_error": False, "skipped": "",
                 "result": TestResult(timed_out=True)}
    with patch.object(acceptance, "generate_acceptance_tests", return_value=gen), \
         patch.object(acceptance, "run_tests_once", return_value=timed_out) as rto:
        suite = acceptance.build_suite("goal", str(root), client=None, model="m")
    assert rto.call_args.kwargs["timeout_sec"] == 240
    assert suite.error == "start run timed out (240s cap)"
    assert not suite.active
