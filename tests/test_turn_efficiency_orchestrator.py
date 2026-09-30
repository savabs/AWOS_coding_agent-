"""
Turn efficiency in the orchestrator (fix 1).

A benchmark run (24 jobs, DeepSeek V4 Flash) found multi-task jobs used 51
turns on average against 20 for single-task ones: the planner split a small
feature into layer tasks, task 1 did the whole goal, and later tasks started
cold, were nudged to edit work that was done, and ran to max_turns and a
resume. These tests pin the four changes:

A. a small plan runs as one task (AWOS_SINGLE_TASK_MAX_FILES);
B. an agent-loop task hands its result to the next task's prompt;
C. no "you have not changed any file" nudge once this goal kept edits;
D. no resume when a max_turns / repeated_tool_call stop left the tests green.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scaffold.agent.agent_loop import AnthropicToolClient, ModelReply, ToolCall
from scaffold.agent.orchestrator import (
    Orchestrator,
    _agent_resume_reason,
    _single_task_max_files,
)
from scaffold.agent.test_runner import TestResult

GREEN = TestResult(passed=3, failed=0, errors=0, pass_rate=1.0, raw_output="", no_tests_found=False)
RED = TestResult(passed=2, failed=1, errors=0, pass_rate=0.67, raw_output="", no_tests_found=False)
NONE = TestResult(passed=0, failed=0, errors=0, pass_rate=0.0, raw_output="", no_tests_found=True)

LAYERS = [
    {"task_id": 1, "file": "models.py", "action": "Add the Refund model", "complexity": "low"},
    {"task_id": 2, "file": "service.py", "action": "Add refund() to the service", "complexity": "high"},
    {"task_id": 3, "file": "cli.py", "files": ["cli.py", "models.py"],
     "action": "Add the refund CLI command", "complexity": "medium"},
]
GOAL = "Support refunds end to end"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in ("AWOS_SINGLE_TASK_MAX_FILES", "AWOS_AGENT_RETRIES",
                 "AWOS_AGENT_MAX_TURNS", "AWOS_MAX_RUN_COST"):
        monkeypatch.delenv(name, raising=False)


# ── A. Collapse small plans ──────────────────────────────────────────────────

def test_small_plan_collapses_into_one_task():
    tasks = Orchestrator._collapse_small_plan(GOAL, LAYERS, 4)
    assert len(tasks) == 1
    task = tasks[0]
    assert task["task_id"] == 1
    assert task["file"] == "models.py"
    assert task["files"] == ["models.py", "service.py", "cli.py"]
    assert task["complexity"] == "high"
    action = task["action"]
    assert action.startswith(GOAL)
    assert "Steps (one session — do them all here, in this order):" in action
    for n, t in enumerate(LAYERS, 1):
        assert f"{n}. {t['action']}" in action
    # Checklist order is the planner's order.
    assert action.index("Refund model") < action.index("refund()") < action.index("CLI command")


def test_plan_over_the_file_threshold_is_kept():
    assert Orchestrator._collapse_small_plan(GOAL, LAYERS, 2) is None
    assert Orchestrator._collapse_small_plan(GOAL, LAYERS, 3) is not None


def test_zero_or_negative_threshold_disables_collapsing():
    assert Orchestrator._collapse_small_plan(GOAL, LAYERS, 0) is None
    assert Orchestrator._collapse_small_plan(GOAL, LAYERS, -1) is None


def test_single_task_plan_is_left_alone():
    assert Orchestrator._collapse_small_plan(GOAL, LAYERS[:1], 4) is None


def test_collapsed_single_file_plan_has_no_files_key():
    tasks = [{"task_id": 1, "file": "a.py", "action": "x", "complexity": "low"},
             {"task_id": 2, "file": "a.py", "action": "y", "complexity": "low"}]
    task = Orchestrator._collapse_small_plan(GOAL, tasks, 4)[0]
    assert task["file"] == "a.py" and "files" not in task


def test_threshold_env(monkeypatch):
    assert _single_task_max_files() == 4
    monkeypatch.setenv("AWOS_SINGLE_TASK_MAX_FILES", "0")
    assert _single_task_max_files() == 0
    monkeypatch.setenv("AWOS_SINGLE_TASK_MAX_FILES", "junk")
    assert _single_task_max_files() == 4


# ── B. Context chains between agent-loop tasks ───────────────────────────────

def test_prompt_with_prior_context_says_confirm_and_finish():
    task = {"action": "Add the CLI", "file": "cli.py", "prev_task_context": "PREVIOUS TASK COMPLETED: x"}
    prompt = Orchestrator._agent_loop_prompt(task, {})
    assert "PREVIOUS TASK COMPLETED: x" in prompt
    assert "confirm it with run_tests and finish without editing" in prompt


def test_prompt_without_prior_context_has_no_such_sentence():
    prompt = Orchestrator._agent_loop_prompt({"action": "Add the CLI"}, {})
    assert "finish without editing" not in prompt


def test_applied_context_only_for_a_successful_task():
    verdict = {"success": True, "summary": "Added refund()", "test_status": "3 passed, 0 failed"}
    ctx = Orchestrator._agent_applied_context({"action": "Add refund"}, ["service.py"], verdict)
    assert "Add refund" in ctx and "service.py" in ctx and "Added refund()" in ctx
    assert "3 passed" in ctx
    assert len(ctx) < 1000
    assert Orchestrator._agent_applied_context({"action": "x"}, [], {"success": False}) == ""


def test_run_task_batch_chains_applied_context():
    orch = Orchestrator.__new__(Orchestrator)
    orch._pause_requested = False
    orch._goal_budget_exhausted = lambda: False
    seen = []

    def fake(task, ctx):
        seen.append(task)
        return {"task_id": task["task_id"], "success": True, "task": task,
                "_applied_context": f"CTX-{task['task_id']}"}

    orch._execute_single_task = fake
    orch._run_task_batch([{"task_id": 1, "action": "a"}, {"task_id": 2, "action": "b"}], {}, False)
    assert "prev_task_context" not in seen[0]
    assert seen[1]["prev_task_context"] == "CTX-1"


# ── Agent-loop harness (scripted model, no network) ──────────────────────────

class _Scripted(AnthropicToolClient):
    def __init__(self, replies):
        super().__init__(client=None, model="claude-haiku-4-5")
        self._replies = list(replies)
        self.prompts = []

    def complete(self, system, messages, registry):
        if len(messages) == 1:
            self.prompts.append(messages[0]["content"])
        return self._replies.pop(0) if self._replies else ModelReply(text="done")


def _call(name, args):
    return ModelReply(text="", tool_calls=[ToolCall(id="t", name=name, arguments=args)],
                      input_tokens=100, output_tokens=20)


FIX = _call("edit_file", {"path": "calc.py", "old_string": "a - b", "new_string": "a + b"})
READ = _call("read_file", {"path": "calc.py"})


def _orchestrator():
    orch = Orchestrator.__new__(Orchestrator)
    orch._gui_bus = None
    orch.execution_log = []
    for name in ("performance", "reward_store", "escalation", "strategy_router",
                 "obs_store", "_feature_extractor", "_update_task_node",
                 "_persist_worker_failure_pattern"):
        setattr(orch, name, MagicMock())
    orch.escalation.failure_count.return_value = 0
    return orch


def _run(script, test_results, orch=None, spy_require_edits=None):
    root = Path(tempfile.mkdtemp())
    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    orch = orch or _orchestrator()
    ctx = {"codebase_root": str(root), "git": MagicMock(), "session": MagicMock(),
           "codebase_context": {}}
    spec = SimpleNamespace(model_id="claude-haiku-4-5", name="Haiku", cost_per_req=0.01,
                           level=SimpleNamespace(value=3))
    client = _Scripted(script)
    runner = MagicMock()
    runner.return_value.run.side_effect = list(test_results)
    from scaffold.agent.agent_loop import AgentLoop
    real_init = AgentLoop.__init__

    def spy(self, *args, **kwargs):
        if spy_require_edits is not None:
            spy_require_edits.append(kwargs.get("require_edits"))
        real_init(self, *args, **kwargs)

    with patch("scaffold.agent.agent_loop.build_client_from_env", return_value=client), \
         patch("scaffold.agent.orchestrator.TestRunner", runner), \
         patch("scaffold.agent.usage_record.record_api_usage"), \
         patch.object(AgentLoop, "__init__", spy):
        out = orch._execute_task_via_agent_loop(
            {"task_id": 1, "action": "fix add in calc.py", "file": "calc.py"}, ctx,
            task_id=1, esc_decision=SimpleNamespace(spec=spec), _span=MagicMock(),
            _strategy=SimpleNamespace(name="default"), _task_ts=0.0, _live_t=None,
            _task_usage={"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
        )
    return SimpleNamespace(out=out, ctx=ctx, root=root, prompts=client.prompts)


def test_agent_loop_result_carries_applied_context():
    run = _run([FIX, ModelReply(text="Fixed add().")], [GREEN])
    assert run.out["success"] is True
    assert "calc.py" in run.out["_applied_context"]
    assert "Fixed add()." in run.out["_applied_context"]


def test_failed_agent_loop_task_carries_no_context(monkeypatch):
    monkeypatch.setenv("AWOS_AGENT_RETRIES", "0")
    run = _run([FIX, ModelReply(text="Done.")], [RED])
    assert run.out["success"] is False
    assert "_applied_context" not in run.out


# ── C. No nudge once this goal kept edits ────────────────────────────────────

def test_require_edits_on_without_earlier_edits():
    flags = []
    _run([FIX, ModelReply(text="ok")], [GREEN], spy_require_edits=flags)
    assert flags == [True]


def test_no_nudge_when_an_earlier_task_kept_edits():
    orch = _orchestrator()
    orch._run_files_changed = {"models.py"}
    flags = []
    run = _run([ModelReply(text="Already done by the previous task.")], [GREEN],
               orch=orch, spy_require_edits=flags)
    assert flags == [False]
    assert len(run.prompts) == 1
    assert run.out["success"] is True  # judged "already satisfied"


# ── D. No resume when a stopped attempt is green ─────────────────────────────

def _judge(stop, files, result, action="fix add in calc.py"):
    orch = Orchestrator.__new__(Orchestrator)
    outcome = SimpleNamespace(stop_reason=stop, final_message="worked on it")
    runner = MagicMock()
    runner.return_value.run.return_value = result
    with patch("scaffold.agent.orchestrator.TestRunner", runner):
        verdict = orch._judge_agent_attempt(
            {"action": action, "file": "calc.py"}, outcome, files, True, "/tmp", None, 1
        )
    return outcome, verdict


@pytest.mark.parametrize("stop", ["max_turns", "repeated_tool_call"])
def test_green_stop_is_a_success_and_not_resumed(stop):
    outcome, verdict = _judge(stop, ["calc.py"], GREEN)
    assert verdict["green_stop"] is True
    assert verdict["success"] is True and verdict["error"] == ""
    assert f"Stopped on {stop} after its tests passed" in verdict["summary"]
    assert _agent_resume_reason(outcome, verdict, 0) is None


@pytest.mark.parametrize("files, result", [
    (["calc.py"], RED),     # tests red: keep going
    (["calc.py"], NONE),    # no tests ran: nothing proves it green
    ([], GREEN),            # no edits of its own
])
def test_stop_that_is_not_green_still_resumes(files, result):
    outcome, verdict = _judge("max_turns", files, result)
    assert verdict["green_stop"] is False
    assert verdict["success"] is False
    assert _agent_resume_reason(outcome, verdict, 0) == "it ran out of turns (max_turns)"


def test_other_stops_are_never_green():
    outcome, verdict = _judge("cost_cap", ["calc.py"], GREEN)
    assert verdict["green_stop"] is False and verdict["success"] is False


def test_writes_tests_red_by_design_is_unchanged():
    outcome, verdict = _judge("completed", ["tests/test_calc.py"], RED,
                              action="Add a test that reproduces the add() bug")
    assert verdict["green_stop"] is False
    assert verdict["test_result"] is None
    assert "fail as expected" in verdict["summary"]


def test_green_stop_end_to_end_runs_one_attempt(monkeypatch):
    # Attempt 1 fixes calc.py, then burns its 2-turn budget; tests are green.
    monkeypatch.setenv("AWOS_AGENT_MAX_TURNS", "2")
    run = _run([FIX, READ, READ], [GREEN, GREEN])
    assert len(run.prompts) == 1
    assert run.out["success"] is True
    run.ctx["git"].rollback_file.assert_not_called()
