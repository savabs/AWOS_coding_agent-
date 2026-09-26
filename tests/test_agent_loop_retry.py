"""
Orchestrator._execute_task_via_agent_loop — resuming after a resumable stop.

Under the agent-loop executor nothing else retries a task, so one stop
(repeated_tool_call, max_turns) used to end the job. A second attempt now
continues in the same workspace, from the first attempt's edits.
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
from scaffold.agent.orchestrator import Orchestrator
from scaffold.agent.test_runner import TestResult


class _Scripted(AnthropicToolClient):
    """Replays replies; records the opening prompt of every attempt."""

    def __init__(self, replies):
        super().__init__(client=None, model="claude-haiku-4-5")  # priced, so a cost cap can trip
        self._replies = list(replies)
        self.prompts = []

    def complete(self, system, messages, registry):
        if len(messages) == 1:
            self.prompts.append(messages[0]["content"])
        if not self._replies:
            return ModelReply(text="done")
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _call(name, args):
    return ModelReply(text="", tool_calls=[ToolCall(id="t", name=name, arguments=args)],
                      input_tokens=100, output_tokens=20)


FIX = _call("edit_file", {"path": "calc.py", "old_string": "a - b", "new_string": "a + b"})
READ = _call("read_file", {"path": "calc.py"})
# The fourth identical call trips repeated_tool_call (DEFAULT_MAX_REPEATS = 3).
STUCK = [READ] * 4
GREEN = TestResult(passed=1, failed=0, errors=0, pass_rate=1.0, raw_output="", no_tests_found=False)
RED = TestResult(passed=0, failed=1, errors=0, pass_rate=0.0, raw_output="", no_tests_found=False)


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


def _run(script, test_results):
    root = Path(tempfile.mkdtemp())
    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    orch = _orchestrator()
    ctx = {"codebase_root": str(root), "git": MagicMock(), "session": MagicMock(),
           "codebase_context": {}}
    spec = SimpleNamespace(model_id="claude-haiku-4-5", name="Haiku", cost_per_req=0.01,
                           level=SimpleNamespace(value=3))
    client = _Scripted(script)
    runner = MagicMock()
    runner.return_value.run.side_effect = list(test_results)
    usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    with patch("scaffold.agent.agent_loop.build_client_from_env", return_value=client), \
         patch("scaffold.agent.orchestrator.TestRunner", runner), \
         patch("scaffold.agent.usage_record.record_api_usage") as record:
        out = orch._execute_task_via_agent_loop(
            {"task_id": 1, "action": "fix add in calc.py", "file": "calc.py"}, ctx,
            task_id=1, esc_decision=SimpleNamespace(spec=spec), _span=MagicMock(),
            _strategy=SimpleNamespace(name="default"), _task_ts=0.0, _live_t=None,
            _task_usage=usage,
        )
    return SimpleNamespace(out=out, ctx=ctx, root=root, prompts=client.prompts, record=record,
                           usage=usage)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in ("AWOS_AGENT_RETRIES", "AWOS_AGENT_MAX_TURNS", "AWOS_MAX_RUN_COST"):
        monkeypatch.delenv(name, raising=False)


def test_resumable_stop_resumes_from_the_edits_and_succeeds():
    finish = _call("edit_file", {"path": "calc.py", "old_string": "return a + b",
                                 "new_string": "return a + b  # fixed"})
    run = _run([FIX, *STUCK, finish, ModelReply(text="Finished.")], [RED, GREEN])

    assert len(run.prompts) == 2
    resume = run.prompts[1]
    assert resume.startswith("fix add in calc.py")
    assert "A previous attempt stopped:" in resume and "repeated_tool_call" in resume
    assert "It changed these files: calc.py" in resume
    assert "Test status: 0 passed, 1 failed" in resume
    assert "do not start over" in resume
    # The second attempt worked on top of the first one's edit.
    assert "return a + b  # fixed" in (run.root / "calc.py").read_text()

    assert run.out["success"] is True
    run.ctx["git"].rollback_file.assert_not_called()
    run.ctx["git"].record_modified.assert_called_once_with(str(run.root / "calc.py"))
    assert run.record.call_count == 2  # both attempts' spend is booked


def test_cost_cap_is_not_resumed(monkeypatch):
    monkeypatch.setenv("AWOS_MAX_RUN_COST", "0.0000001")
    run = _run([FIX], [RED, GREEN])
    assert len(run.prompts) == 1
    assert run.out["success"] is False
    assert "cost_cap" in run.out["verify_error"]


def test_retries_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("AWOS_AGENT_RETRIES", "0")
    run = _run([FIX, *STUCK], [RED, GREEN])
    assert len(run.prompts) == 1
    assert run.out["success"] is False
    run.ctx["git"].rollback_file.assert_called_once_with(str(run.root / "calc.py"))


def test_two_failed_attempts_roll_back_once():
    run = _run([FIX, *STUCK, *STUCK], [RED, RED])
    assert len(run.prompts) == 2
    assert run.out["success"] is False
    assert "after 2 attempts" in run.out["verify_error"]
    run.ctx["git"].rollback_file.assert_called_once_with(str(run.root / "calc.py"))
    run.ctx["git"].record_modified.assert_not_called()


def test_model_error_is_resumed_only_once(monkeypatch):
    monkeypatch.setenv("AWOS_AGENT_RETRIES", "3")
    run = _run([RuntimeError("provider down")] * 3, [RED] * 3)
    assert len(run.prompts) == 2
    assert "provider down" in run.prompts[1]
    assert run.out["success"] is False


def test_resumed_attempt_is_not_nudged_to_edit_after_earlier_edits(monkeypatch):
    # Attempt 1 fixes calc.py and runs out of turns; the tests are green.
    # Attempt 2 finds the work done. It used to be told "you have not changed
    # any file" twice, run out of turns on the nudges, and roll the fix back.
    monkeypatch.setenv("AWOS_AGENT_MAX_TURNS", "2")
    done = ModelReply(text="Already done.", input_tokens=10, output_tokens=5)
    run = _run([FIX, READ, done, done, done], [GREEN, GREEN])
    assert len(run.prompts) == 2
    assert run.out["success"] is True
    assert "return a + b" in (run.root / "calc.py").read_text()
    run.ctx["git"].rollback_file.assert_not_called()


def test_resumed_attempt_draws_on_the_same_cap(monkeypatch):
    monkeypatch.setenv("AWOS_MAX_RUN_COST", "0.0015")
    from scaffold.agent.agent_loop import AgentLoop
    caps = []
    real_init = AgentLoop.__init__

    def spy(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        caps.append(self.max_cost_usd)

    # Attempt 1: FIX + 4 identical reads = 5 turns, $0.0010, repeated_tool_call.
    with patch.object(AgentLoop, "__init__", spy):
        run = _run([FIX, *STUCK, *STUCK], [RED, RED])
    assert len(caps) == 2
    # The resume gets the $0.0005 left, not a fresh $0.0015.
    assert caps[1] == pytest.approx(0.0015 - 0.0010)
    # The whole task stays within the cap plus the one turn a loop may
    # overshoot by (it checks after the turn); a fresh cap allowed $0.0030.
    # Both attempts' spend also reaches the task's usage, so reward and span.
    assert run.usage["cost_usd"] == pytest.approx(0.0016)


def test_no_retry_once_the_task_cost_cap_is_used_up(monkeypatch):
    # Attempt 1 stops on repeated_tool_call at $0.0010 of a $0.0011 cap:
    # less than one more turn is left, so the resumable stop is not resumed.
    monkeypatch.setenv("AWOS_MAX_RUN_COST", "0.0011")
    run = _run([FIX, *STUCK, *STUCK], [RED, RED])
    assert len(run.prompts) == 1
    assert run.out["success"] is False


def test_files_a_command_wrote_count_as_the_task_s_edits():
    # A task done through run_command (a generator, sed -i) was judged "no
    # edits", resumed with "It changed these files: none", and failed.
    from scaffold.agent.agent_loop import AgentLoop
    real_run = AgentLoop.run

    def run(self, prompt):
        outcome = real_run(self, prompt)
        path = os.path.join(self.registry.project_root, "calc.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("def add(a, b):\n    return a + b\n")
        outcome.files_written = [path]
        return outcome

    with patch.object(AgentLoop, "run", run):
        run_ = _run([ModelReply(text="Regenerated calc.py.")], [GREEN])
    assert len(run_.prompts) == 1
    assert run_.out["success"] is True
    run_.ctx["git"].record_modified.assert_called_once_with(str(run_.root / "calc.py"))


def test_only_files_git_does_not_ignore_count(tmp_path):
    import subprocess
    from scaffold.agent.orchestrator import _not_ignored
    paths = [str(tmp_path / "calc.py"), str(tmp_path / ".coverage")]
    assert _not_ignored(str(tmp_path), paths) == paths  # not a repo: all kept
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text(".coverage\n", encoding="utf-8")
    assert _not_ignored(str(tmp_path), paths) == paths[:1]


def test_agent_retries_are_clamped(monkeypatch):
    from scaffold.agent.orchestrator import MAX_AGENT_RETRIES, _agent_retries
    monkeypatch.setenv("AWOS_AGENT_RETRIES", "50")
    assert _agent_retries() == MAX_AGENT_RETRIES


@pytest.mark.parametrize("action, writes_tests", [
    ("Add a test that reproduces the add() bug", True),
    ("Write regression tests for --to", True),
    ("Create test_export.py covering the new flag", False),
    ("Add the --status flag; the check is pytest", False),
    ("Add caching to load(); keep the tests passing", False),
    ("Fix the address parser so every test passes", False),
    ("Update the handler that adds retries; run the tests", False),
])
def test_asks_for_tests_is_about_writing_tests(action, writes_tests):
    from scaffold.agent.orchestrator import _asks_for_tests
    assert _asks_for_tests({"action": action}) is writes_tests
