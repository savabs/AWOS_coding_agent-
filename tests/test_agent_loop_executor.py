"""
Orchestrator._execute_task_via_agent_loop — docs/specs/agent_loop_executor_spec.md.

The loop is driven by a scripted client; tests are the orchestrator's own
verification, so they are stubbed to pass or fail.
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
from scaffold.agent.orchestrator import Orchestrator, executor_choice
from scaffold.agent.test_runner import TestResult


class _Scripted(AnthropicToolClient):
    def __init__(self, replies):
        super().__init__(client=None, model="claude-test")
        self._replies = list(replies)

    def complete(self, system, messages, registry):
        return self._replies.pop(0) if self._replies else ModelReply(text="done")


def _call(name, args):
    return ModelReply(text="", tool_calls=[ToolCall(id="t", name=name, arguments=args)],
                      input_tokens=100, output_tokens=20)


EDIT = [
    _call("read_file", {"path": "calc.py"}),
    _call("edit_file", {"path": "calc.py", "old_string": "a - b", "new_string": "a + b"}),
    ModelReply(text="Fixed add().", input_tokens=100, output_tokens=20),
]


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


def _run(script, test_result=None):
    root = Path(tempfile.mkdtemp())
    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    orch = _orchestrator()
    ctx = {"codebase_root": str(root), "git": MagicMock(), "session": MagicMock(),
           "codebase_context": {}}
    spec = SimpleNamespace(model_id="claude-haiku-4-5", name="Haiku", cost_per_req=0.01,
                           level=SimpleNamespace(value=3))
    runner = MagicMock()
    runner.return_value.run.return_value = test_result or TestResult(
        passed=1, failed=0, errors=0, pass_rate=1.0, raw_output="", no_tests_found=False)
    with patch("scaffold.agent.agent_loop.build_client_from_env", return_value=_Scripted(script)), \
         patch("scaffold.agent.orchestrator.TestRunner", runner):
        out = orch._execute_task_via_agent_loop(
            {"task_id": 1, "action": "fix add in calc.py", "file": "calc.py"}, ctx,
            task_id=1, esc_decision=SimpleNamespace(spec=spec), _span=MagicMock(),
            _strategy=SimpleNamespace(name="default"), _task_ts=0.0, _live_t=None,
            _task_usage={"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
        )
    return out, ctx, root


def test_edit_that_passes_tests_succeeds_and_is_recorded():
    out, ctx, root = _run(EDIT)
    assert out["success"] is True
    assert out["agent_loop"] is True
    assert "a + b" in (root / "calc.py").read_text()
    ctx["git"].record_modified.assert_called_once_with(str(root / "calc.py"))
    ctx["git"].rollback_file.assert_not_called()


def test_failing_tests_fail_the_task_and_roll_back():
    failing = TestResult(passed=0, failed=1, errors=0, pass_rate=0.0,
                         raw_output="", no_tests_found=False)
    out, ctx, root = _run(EDIT, test_result=failing)
    assert out["success"] is False
    ctx["git"].rollback_file.assert_called_once_with(str(root / "calc.py"))


def test_finishing_without_an_edit_is_a_failure():
    # The ReAct failure mode: claim done, change nothing.
    out, ctx, _ = _run([ModelReply(text="All tests now pass.", input_tokens=10, output_tokens=5)])
    assert out["success"] is False
    assert out["failure_kind"] == "agent_loop_fail"
    assert "without editing" in out["verify_error"]


def test_no_model_client_falls_back():
    orch = _orchestrator()
    with patch("scaffold.agent.agent_loop.build_client_from_env", side_effect=RuntimeError("no key")):
        out = orch._execute_task_via_agent_loop(
            {"task_id": 1, "action": "fix", "file": "x.py"},
            {"codebase_root": tempfile.mkdtemp(), "git": MagicMock(), "session": MagicMock()},
            task_id=1,
            esc_decision=SimpleNamespace(spec=SimpleNamespace(model_id="m", name="m")),
            _span=MagicMock(), _strategy=SimpleNamespace(name="d"), _task_ts=0.0,
            _live_t=None, _task_usage={},
        )
    assert out is None


@pytest.mark.parametrize("value, expected", [
    (None, "agent_loop"), ("react", "react"), ("WORKER", "worker"),
])
def test_executor_choice(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("AWOS_EXECUTOR", raising=False)
    else:
        monkeypatch.setenv("AWOS_EXECUTOR", value)
    assert executor_choice() == expected
