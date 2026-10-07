"""
scaffold/agent/acceptance.py and its orchestrator hooks (ablation C,
docs/specs/ablation_acceptance.md).

No network: the model is a fake OpenAI-shaped client; the filter runs real
pytest on a tmp project (no sandbox).
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scaffold.agent import acceptance
from scaffold.agent.acceptance import (
    AcceptanceSuite, build_suite, filter_start_failing, parse_outcomes, run_acceptance,
)
from scaffold.agent.agent_loop import AnthropicToolClient, ModelReply, ToolCall
from scaffold.agent.orchestrator import Orchestrator
from scaffold.agent.test_runner import TestResult


def _project() -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "pkg").mkdir()
    (root / "pkg" / "__init__.py").write_text("from .calc import add\n", encoding="utf-8")
    (root / "pkg" / "calc.py").write_text(
        "def add(a, b):\n    return a - b\n\n\ndef mul(a, b):\n    return a * b\n",
        encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_calc.py").write_text(
        "from pkg.calc import mul\n\n\ndef test_mul():\n    assert mul(2, 3) == 6\n",
        encoding="utf-8")
    return root


class _FakeClient:
    def __init__(self, reply=""):
        self.reply, self.calls = reply, []
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply),
                                     finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=3000, completion_tokens=300),
        )


GENERATED = '''```python
def test_already_true():
    from pkg.calc import mul
    assert mul(2, 2) == 4


def test_add_adds():
    from pkg import add
    assert add(1, 2) == 3


def test_new_name_exists():
    from pkg.calc import sub
    assert sub(3, 1) == 2


def test_buggy_test():
    assert undefined_helper() == 1
```'''


@pytest.fixture(autouse=True)
def _safe(monkeypatch):
    monkeypatch.setenv("AWOS_SAFE_TO_RUN_TESTS", "1")


# ── module ──────────────────────────────────────────────────────────────────

def test_filter_keeps_only_start_failing_tests():
    root = _project()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    client = _FakeClient(GENERATED)
    suite = build_suite("add(1, 2) should be 3; add pkg.calc.sub", str(root),
                        client=client, model="m")
    assert suite.generated == 4
    # passing-on-start dropped; ImportError of a promised name kept; NameError dropped
    assert sorted(suite.kept) == ["test_add_adds", "test_new_name_exists"]
    assert suite.dropped == {"passed on start": 1, "test bug": 1}
    assert suite.active
    # reasoning off, one call, modest cap
    assert len(client.calls) == 1
    # nothing left in the project: not in git status, so never in the patch
    assert not list(root.glob(".awos_acceptance_*"))
    status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"],
                            cwd=root, capture_output=True, text=True).stdout
    assert ".awos_acceptance" not in status and "test_awos_acceptance" not in status


def test_run_acceptance_fails_then_passes_after_fix():
    root = _project()
    suite = build_suite("fix add", str(root), client=_FakeClient(GENERATED), model="m")
    ok, summary, feedback = run_acceptance(str(root), suite)
    assert ok is False and summary == "2 failing of 2"
    assert "test_add_adds" in feedback and "Acceptance tests" in feedback
    (root / "pkg" / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n\n\n"
        "def mul(a, b):\n    return a * b\n", encoding="utf-8")
    ok, summary, feedback = run_acceptance(str(root), suite)
    assert ok is True and summary == "2 kept, 2 passed" and feedback == ""
    assert not list(root.glob(".awos_acceptance_*"))


def test_gate_inactive_when_nothing_fails_on_start():
    root = _project()
    reply = "```python\ndef test_ok():\n    from pkg.calc import mul\n    assert mul(1, 1) == 1\n```"
    suite = build_suite("x", str(root), client=_FakeClient(reply), model="m")
    assert suite.generated == 1 and suite.kept == [] and not suite.active
    assert run_acceptance(str(root), suite) == (True, "inactive", "")


def test_syntax_error_drops_all():
    root = _project()
    reply = "```python\ndef test_a(:\n    assert False\n```"
    suite = build_suite("x", str(root), client=_FakeClient(reply), model="m")
    assert not suite.active


def test_parse_outcomes_and_collection_error():
    out = ("FAILED d/test_x.py::test_a - AssertionError: no\n"
           "PASSED d/test_x.py::test_b\nERROR d/test_x.py::test_c - fixture 'foo' not found\n")
    outcomes, coll = parse_outcomes(out)
    assert outcomes["test_a"][0] == "FAILED" and outcomes["test_b"][0] == "PASSED"
    assert not coll
    assert filter_start_failing(outcomes, coll)[0] == ["test_a"]
    _, coll = parse_outcomes("ERROR d/test_x.py - ImportError\n1 error during collection\n")
    assert coll and filter_start_failing({}, coll)[0] == []


def test_env_default_off(monkeypatch):
    monkeypatch.delenv("AWOS_ACCEPTANCE", raising=False)
    assert acceptance.acceptance_enabled() is False
    orch = Orchestrator.__new__(Orchestrator)
    with patch("scaffold.agent.one_shot.one_shot_client") as mk:
        assert orch._acceptance_prepare({"action": "x"}, {}, "m", "/nonexistent", None) is None
    mk.assert_not_called()
    monkeypatch.setenv("AWOS_ACCEPTANCE", "1")
    assert acceptance.acceptance_enabled() is True


# ── orchestrator hooks ──────────────────────────────────────────────────────

class _Scripted(AnthropicToolClient):
    def __init__(self, replies):
        super().__init__(client=None, model="claude-test")
        self._replies = list(replies)
        self.prompts = []

    def complete(self, system, messages, registry):
        if not self.prompts or messages[0]["content"] != self.prompts[-1]:
            if len(messages) == 1:
                self.prompts.append(messages[0]["content"])
        return self._replies.pop(0) if self._replies else ModelReply(text="done")


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


GREEN = TestResult(passed=3, failed=0, errors=0, pass_rate=1.0, raw_output="3 passed",
                   no_tests_found=False)

FIX = """pkg/calc.py
```python
<<<<<<< SEARCH
def add(a, b):
    return a - b
=======
def add(a, b):
    return a + b
>>>>>>> REPLACE
```
"""


def _run(acc_results, *, one_shot="1", script=None, retries="1"):
    root = _project()
    orch = _orchestrator()
    orch._run_files_changed = set()
    ctx = {"codebase_root": str(root), "git": MagicMock(), "session": MagicMock(),
           "codebase_context": {}, "exploration": {}}
    spec = SimpleNamespace(model_id="claude-haiku-4-5", name="Haiku", cost_per_req=0.01,
                           level=SimpleNamespace(value=3))
    runner = MagicMock()
    runner.return_value.run.return_value = GREEN
    scripted = _Scripted(script or [ModelReply(text="Done.", input_tokens=100, output_tokens=20)])
    suite = AcceptanceSuite(source="def test_a():\n    assert 0\n", generated=1,
                            kept=["test_a"], cost_usd=0.001)
    check = MagicMock(side_effect=list(acc_results))
    with patch.dict(os.environ, {"AWOS_ONE_SHOT": one_shot, "AWOS_ACCEPTANCE": "1",
                                 "AWOS_AGENT_RETRIES": retries}), \
         patch("scaffold.agent.agent_loop.build_client_from_env", return_value=scripted), \
         patch("scaffold.agent.one_shot.one_shot_client", return_value=_FakeClient(FIX)), \
         patch("scaffold.agent.acceptance.build_suite", return_value=suite), \
         patch("scaffold.agent.acceptance.run_acceptance", check), \
         patch("scaffold.agent.orchestrator.TestRunner", runner):
        out = orch._execute_task_via_agent_loop(
            {"task_id": 1, "action": "fix add in pkg/calc.py", "file": "pkg/calc.py"}, ctx,
            task_id=1, esc_decision=SimpleNamespace(spec=spec), _span=MagicMock(),
            _strategy=SimpleNamespace(name="default"), _task_ts=0.0, _live_t=None,
            _task_usage={"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
        )
    return SimpleNamespace(out=out, scripted=scripted, check=check, orch=orch, root=root)


def _edit_then_done(text="Done."):
    return [ModelReply(text="", tool_calls=[ToolCall(id="t", name="edit_file", arguments={
                "path": "pkg/calc.py", "old_string": "a - b", "new_string": "a + b"})],
                input_tokens=100, output_tokens=20),
            ModelReply(text=text, input_tokens=100, output_tokens=20)]


FAIL = (False, "1 failing of 1", "ACC-FEEDBACK: test_a failed")
PASS = (True, "1 kept, 1 passed", "")


def test_one_shot_not_solved_when_acceptance_fails(capsys):
    r = _run([FAIL, PASS])
    log = capsys.readouterr().out
    assert "[ACCEPTANCE] generated 1 tests, kept 1 (failed on start), $0.0010" in log
    assert "[ONE-SHOT] fell back to the agent loop: acceptance: 1 failing of 1" in log
    assert r.out.get("one_shot") is not True and r.out.get("agent_loop") is True
    assert r.scripted.prompts and "ACC-FEEDBACK" in r.scripted.prompts[0]
    assert r.out["success"] is True
    assert r.check.call_count == 2
    assert r.orch._acceptance_suite is None          # cleaned up


def test_one_shot_solved_when_acceptance_passes(capsys):
    r = _run([PASS])
    assert r.out["one_shot"] is True and r.scripted.prompts == []
    assert "[ONE-SHOT] solved in 1 call" in capsys.readouterr().out


def test_agent_loop_resumes_on_failing_acceptance(capsys):
    r = _run([FAIL, PASS], one_shot="0",
             script=_edit_then_done() + [ModelReply(text="Done again.", input_tokens=100,
                                                    output_tokens=20)])
    log = capsys.readouterr().out
    assert "AgentLoop resuming after: acceptance tests failing (1 failing of 1)" in log
    assert len(r.scripted.prompts) == 2 and "ACC-FEEDBACK" in r.scripted.prompts[1]
    assert r.out["success"] is True


def test_agent_loop_keeps_verdict_when_retries_exhausted(capsys):
    r = _run([FAIL], one_shot="0", retries="0", script=_edit_then_done())
    log = capsys.readouterr().out
    assert "[ACCEPTANCE] still failing at the end" in log
    assert r.out["success"] is True                  # visible-test verdict stands, no rollback
