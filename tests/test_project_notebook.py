"""
Project notebook — docs/specs/compounding_proof_spec.md, piece A.

Hermetic: every test runs in a temporary working directory (the notebook is
cwd-relative state), the update's model is a fake OpenAI-shaped client, and
the ledger write is patched out.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scaffold.agent import project_notebook as nb
from scaffold.agent.agent_loop import LoopOutcome
from scaffold.agent.orchestrator import Orchestrator

GOOD = """## How to work here
- Tests: `python -m pytest -q`
## Layout and conventions
- Errors: raise OrderToolError (ordertool/errors.py)
## Pitfalls
- --to excluded the last day; fixed with an end-of-day bound
## Past jobs
- add CSV export -> solved
"""


@pytest.fixture(autouse=True)
def _in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AWOS_NOTEBOOK", "1")
    monkeypatch.delenv("AWOS_PROJECT_ID", raising=False)
    monkeypatch.delenv("AWOS_NOTEBOOK_MODEL", raising=False)


class _FakeClient:
    """OpenAI-shaped: client.chat.completions.create(...)."""

    def __init__(self, reply="", error=None):
        self.calls = []
        self._reply, self._error = reply, error
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._reply))],
            usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=500),
        )


def _update(client, root="proj", trace="TASK: x\n - run_tests() ok", **kw):
    with patch("scaffold.agent.usage_record.record_api_usage") as record:
        out = nb.update_notebook(root, "add a flag", {"success": True}, trace,
                                 client=client, **kw)
    return out, record


# ── Where it lives ───────────────────────────────────────────────────────────


def test_project_id_hashes_the_resolved_root(tmp_path):
    (tmp_path / "proj").mkdir()
    expected = hashlib.sha256(str((tmp_path / "proj").resolve()).encode()).hexdigest()[:12]
    assert nb.project_id("proj") == expected
    assert nb.project_id(str(tmp_path / "proj")) == expected
    assert nb.notebook_path("proj") == Path(".awos/projects") / expected / "notebook.md"


def test_project_id_env_wins_and_cannot_escape(monkeypatch):
    monkeypatch.setenv("AWOS_PROJECT_ID", "ordertool")
    assert nb.project_id("anything") == "ordertool"
    monkeypatch.setenv("AWOS_PROJECT_ID", "../../etc")
    assert "/" not in nb.project_id("anything")
    assert not nb.project_id("anything").startswith(".")


# ── Read ─────────────────────────────────────────────────────────────────────


def test_read_missing_or_empty_is_empty():
    assert nb.read_notebook("proj") == ""
    path = nb.notebook_path("proj")
    path.parent.mkdir(parents=True)
    path.write_text("  \n", encoding="utf-8")
    assert nb.read_notebook("proj") == ""


def test_read_is_capped_and_prints_the_marker(capsys):
    path = nb.notebook_path("proj")
    path.parent.mkdir(parents=True)
    path.write_text("x" * 9000, encoding="utf-8")
    text = nb.read_notebook("proj")
    assert len(text) == nb.READ_CAP == 4000
    assert f"[notebook] read 4000 chars from {path}" in capsys.readouterr().out


def test_prompt_section_has_the_header():
    section = nb.prompt_section(GOOD)
    assert section.startswith(
        "What you learned about this project on earlier jobs (may be out of date; "
        "check before relying on it):\n")
    assert "OrderToolError" in section
    assert nb.prompt_section("") == ""


def test_switch_off_disables_read_and_write(monkeypatch):
    path = nb.notebook_path("proj")
    path.parent.mkdir(parents=True)
    path.write_text(GOOD, encoding="utf-8")
    monkeypatch.setenv("AWOS_NOTEBOOK", "0")
    assert nb.read_notebook("proj") == ""
    client = _FakeClient(GOOD.replace("solved", "failed"))
    out, record = _update(client)
    assert out is None and client.calls == []
    record.assert_not_called()
    assert path.read_text(encoding="utf-8") == GOOD


# ── Write ────────────────────────────────────────────────────────────────────


def test_update_rewrites_the_notebook_and_records_spend(capsys):
    client = _FakeClient(GOOD)
    out, record = _update(client)
    path = nb.notebook_path("proj")
    assert out == path.read_text(encoding="utf-8")
    assert all(section in out for section in nb.SECTIONS)
    call = client.calls[0]
    assert call["model"] == nb.DEFAULT_NOTEBOOK_MODEL
    assert "add a flag" in call["messages"][1]["content"]
    assert "run_tests" in call["messages"][1]["content"]
    kwargs = record.call_args.kwargs
    assert kwargs["request_type"] == "notebook"
    assert kwargs["input_tokens"] == 1000 and kwargs["output_tokens"] == 500
    printed = capsys.readouterr().out
    assert f"[notebook] updated {path} ({len(out)} chars, $" in printed


def test_update_gets_the_old_notebook_and_the_env_model(monkeypatch):
    path = nb.notebook_path("proj")
    path.parent.mkdir(parents=True)
    path.write_text("## Pitfalls\n- OLD FACT\n", encoding="utf-8")
    monkeypatch.setenv("AWOS_NOTEBOOK_MODEL", "anthropic/claude-haiku-4.5")
    client = _FakeClient(GOOD)
    _update(client)
    assert client.calls[0]["model"] == "anthropic/claude-haiku-4.5"
    assert "OLD FACT" in client.calls[0]["messages"][1]["content"]


def test_update_enforces_size_sections_and_past_jobs():
    jobs = "\n".join(f"- job {i} -> solved" for i in range(40))
    reply = ("Sure! Here is the notebook.\n```markdown\n"
             "## How to work here\n" + "\n".join(f"- cmd {i} " + "y" * 80 for i in range(40))
             + "\n## Secrets\n- should be dropped\n"
             + "## Layout and conventions\n- errors: OrderToolError\n"
             + "## Pitfalls\n- none yet\n"
             + "## Past jobs\n" + jobs + "\n```")
    out, _ = _update(_FakeClient(reply))
    assert len(out) <= nb.MAX_NOTEBOOK_CHARS
    assert "should be dropped" not in out and "Secrets" not in out
    assert "Sure!" not in out and "```" not in out
    headings = [line for line in out.splitlines() if line.startswith("#")]
    assert headings == list(nb.SECTIONS)
    past = out.split("## Past jobs\n", 1)[1].strip().splitlines()
    assert len(past) <= nb.MAX_PAST_JOBS
    assert past[-1] == "- job 39 -> solved"  # the newest are kept
    assert "OrderToolError" in out


@pytest.mark.parametrize("client", [
    _FakeClient("I cannot help with that."),
    _FakeClient(""),
    _FakeClient(error=RuntimeError("503")),
])
def test_failed_or_garbage_update_keeps_the_old_notebook(client):
    path = nb.notebook_path("proj")
    path.parent.mkdir(parents=True)
    path.write_text(GOOD, encoding="utf-8")
    out, _ = _update(client)
    assert out is None
    assert path.read_text(encoding="utf-8") == GOOD


def test_no_client_or_no_trace_skips_the_update(capsys):
    assert nb.update_notebook("proj", "g", {"success": True}, "TASK: x") is None  # no key
    assert "update skipped" in capsys.readouterr().out
    client = _FakeClient(GOOD)
    out, _ = _update(client, trace="")
    assert out is None and client.calls == []
    assert not nb.notebook_path("proj").exists()


# ── The trace ────────────────────────────────────────────────────────────────


def _outcome(n_calls, detail="x" * 150):
    return LoopOutcome(
        success=True, stop_reason="completed",
        transcript=[{"turn": i, "text": "", "calls": [
            {"name": "run_command", "ok": i % 2 == 0, "args": f"python -m pytest -k t{i}",
             "detail": detail}]} for i in range(n_calls)],
    )


def test_task_trace_has_calls_errors_and_result():
    outcome = LoopOutcome(success=True, stop_reason="completed", transcript=[
        {"turn": 1, "text": "", "calls": [
            {"name": "run_tests", "ok": True, "args": "", "detail": "3 passed"},
            {"name": "edit_file", "ok": False, "args": "ordertool/cli.py",
             "detail": "old_string not found"}]}])
    trace = nb.task_trace({"action": "add --json"}, [outcome],
                          {"success": True, "error": "", "test_status": "3 passed, 0 failed"})
    assert "TASK: add --json" in trace
    assert "run_tests() ok: 3 passed" in trace
    assert "edit_file(ordertool/cli.py) ERROR: old_string not found" in trace
    assert "tests: 3 passed, 0 failed" in trace


def test_compact_trace_is_capped_and_keeps_every_task():
    traces = [nb.task_trace({"action": f"task {i}"}, [_outcome(100)]) for i in range(4)]
    assert sum(len(t) for t in traces) > 6000
    compact = nb.compact_trace(traces)
    assert len(compact) <= nb.MAX_TRACE_CHARS == 6000
    for i in range(4):
        assert f"TASK: task {i}" in compact
    assert "stop: completed" in compact  # each task's ending survives
    assert nb.compact_trace([]) == ""


# ── Orchestrator hooks ───────────────────────────────────────────────────────


def test_prompt_includes_the_notebook_only_when_present():
    task = {"action": "add --json to export", "file": "ordertool/cli.py"}
    with_nb = Orchestrator._agent_loop_prompt(task, {"notebook": GOOD})
    assert nb.HEADER in with_nb and "OrderToolError" in with_nb
    without = Orchestrator._agent_loop_prompt(task, {})
    assert nb.HEADER not in without
    assert Orchestrator._agent_loop_prompt(task, {"notebook": ""}) == without


def test_orchestrator_reads_once_and_updates_from_the_goals_traces(capsys):
    path = nb.notebook_path("proj")
    path.parent.mkdir(parents=True)
    path.write_text(GOOD, encoding="utf-8")
    assert Orchestrator._read_project_notebook("proj") == GOOD.strip()

    orch = Orchestrator.__new__(Orchestrator)
    orch.tracker = None
    orch._notebook_traces = [nb.task_trace(
        {"action": "add --json"}, [_outcome(2, detail="ok")],
        {"success": True, "error": "", "test_status": "4 passed, 0 failed"})]
    client = _FakeClient(GOOD.replace("solved", "solved twice"))
    with patch.object(nb, "_default_client", return_value=client), \
         patch("scaffold.agent.usage_record.record_api_usage"):
        orch._update_project_notebook("add --json", "proj", {"success": True})
    prompt = client.calls[0]["messages"][1]["content"]
    assert "tests: 4 passed, 0 failed" in prompt and "TASK: add --json" in prompt
    assert "solved twice" in path.read_text(encoding="utf-8")
    # No goal check ran: the job goes in as unverified, whatever the tests say.
    assert f"verification: {nb.LEVEL_TESTS_ONLY}" in prompt
    assert "success: True" not in prompt
    assert f"[notebook] job recorded as: {nb.LEVEL_TESTS_ONLY}" in capsys.readouterr().out


# ── Honest verification levels ───────────────────────────────────────────────
# The notebook once recorded "success: 25 tests passed" for a job whose hidden
# acceptance tests failed: it only ever saw the project's own tests.

_VERIFIED_CHECK = {"verified": True, "source": "tool", "complete": True,
                   "reasoning": "all 4 read sites migrated"}
_NOT_RUN = {"verified": False, "source": "not_run", "complete": True, "reasoning": ""}


def test_verified_only_when_the_goal_check_verified_complete():
    outcome = {"success": True, "tests": "25 passed, 0 failed", "goal_check": _VERIFIED_CHECK}
    assert nb.verification_level(outcome) == nb.LEVEL_VERIFIED


def test_tests_passing_without_a_goal_check_is_not_verified():
    for check in (None, _NOT_RUN):
        outcome = {"success": True, "tests": "25 passed, 0 failed", "goal_check": check}
        assert nb.verification_level(outcome) == nb.LEVEL_TESTS_ONLY


def test_fail_open_goal_check_is_not_verified():
    check = {"verified": False, "source": "fallback", "complete": True,
             "reasoning": "checker ran out of turns"}
    level = nb.verification_level(
        {"success": True, "tests": "25 passed, 0 failed", "goal_check": check})
    assert level.startswith(nb.LEVEL_TESTS_ONLY)
    assert "checker ran out of turns" in level
    assert nb.LEVEL_VERIFIED != level


def test_success_without_passing_tests_or_check_is_not_verified():
    for tests in (None, "no tests ran", "0 passed, 0 failed"):
        level = nb.verification_level({"success": True, "tests": tests})
        assert level == nb.LEVEL_UNCHECKED


def test_incomplete_carries_the_goal_checks_reason():
    check = {"verified": True, "source": "tool", "complete": False,
             "reasoning": "--to still excludes the last day"}
    level = nb.verification_level(
        {"success": False, "tasks_failed": 0, "tests": "25 passed, 0 failed",
         "goal_check": check})
    assert level == "incomplete: --to still excludes the last day"


def test_failed_carries_a_reason():
    assert nb.verification_level({"success": False, "tasks_failed": 2}) == \
        "failed: 2 task(s) failed"
    level = nb.verification_level({"success": False, "tasks_failed": 1,
                                   "incomplete_reason": "goal budget used up"})
    assert level == "failed: goal budget used up"


def test_update_prompt_states_the_level_not_success():
    outcome = {"success": True, "tests": "25 passed, 0 failed", "goal_check": _NOT_RUN}
    prompt = nb.build_update_prompt("", "add a flag", outcome, "TASK: x")
    assert f"verification: {nb.LEVEL_TESTS_ONLY}" in prompt
    assert "not proof the goal is met" in prompt
    assert "goal check: not run" in prompt
    assert "success" not in prompt.split("ACTION TRACE")[0]


def test_system_prompt_forbids_unchecked_claims_and_marks_pitfalls_provisional():
    text = " ".join(nb.SYSTEM_PROMPT.split())
    assert "Never write that something works" in text
    assert "unless the trace or outcome shows it was checked" in text
    assert "provisional" in text and "(unverified)" in text
    assert nb.LEVEL_TESTS_ONLY in text
    assert 'never as "success" or "solved"' in text


def test_orchestrator_passes_the_goal_checks_verdict_to_the_notebook():
    orch = Orchestrator.__new__(Orchestrator)
    orch.tracker = None
    orch._last_goal_verdict = SimpleNamespace(
        verified=True, complete=False, source="tool", reasoning="export ignores --to")
    orch._notebook_traces = [nb.task_trace(
        {"action": "add --to"}, [_outcome(1, detail="ok")],
        {"success": True, "error": "", "test_status": "25 passed, 0 failed"})]
    client = _FakeClient(GOOD)
    with patch.object(nb, "_default_client", return_value=client), \
         patch("scaffold.agent.usage_record.record_api_usage"):
        orch._update_project_notebook("add --to", "proj", {
            "success": False, "tasks_completed": 1, "tasks_failed": 0,
            "goal_check": orch._goal_check_summary(False, "export ignores --to"),
            "incomplete_reason": "",
        })
    prompt = client.calls[0]["messages"][1]["content"]
    assert "verification: incomplete: export ignores --to" in prompt


def test_agent_loop_task_leaves_a_trace():
    # The executor appends one trace per task to the goal's list.
    from scaffold.agent.agent_loop import AnthropicToolClient, ModelReply, ToolCall
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

    script = [
        _call("read_file", {"path": "calc.py"}),
        _call("edit_file", {"path": "calc.py", "old_string": "a - b", "new_string": "a + b"}),
        ModelReply(text="Fixed add().", input_tokens=100, output_tokens=20),
    ]
    root = Path("work").resolve()
    root.mkdir()
    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    orch = Orchestrator.__new__(Orchestrator)
    orch._gui_bus = None
    orch.execution_log = []
    for name in ("performance", "reward_store", "escalation", "strategy_router",
                 "obs_store", "_feature_extractor", "_update_task_node",
                 "_persist_worker_failure_pattern"):
        setattr(orch, name, MagicMock())
    orch.escalation.failure_count.return_value = 0
    orch._run_files_changed = set()
    orch._notebook_traces = []
    ctx = {"codebase_root": str(root), "git": MagicMock(), "session": MagicMock(),
           "codebase_context": {}}
    spec = SimpleNamespace(model_id="claude-haiku-4-5", name="Haiku", cost_per_req=0.01,
                           level=SimpleNamespace(value=3))
    runner = MagicMock()
    runner.return_value.run.return_value = TestResult(
        passed=1, failed=0, errors=0, pass_rate=1.0, raw_output="", no_tests_found=False)
    with patch("scaffold.agent.agent_loop.build_client_from_env", return_value=_Scripted(script)), \
         patch("scaffold.agent.orchestrator.TestRunner", runner), \
         patch("scaffold.agent.usage_record.record_api_usage"):
        orch._execute_task_via_agent_loop(
            {"task_id": 1, "action": "fix add in calc.py", "file": "calc.py"}, ctx,
            task_id=1, esc_decision=SimpleNamespace(spec=spec), _span=MagicMock(),
            _strategy=SimpleNamespace(name="default"), _task_ts=0.0, _live_t=None,
            _task_usage={"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
        )
    assert len(orch._notebook_traces) == 1
    trace = orch._notebook_traces[0]
    assert "TASK: fix add in calc.py" in trace
    assert "read_file(calc.py) ok" in trace
    assert "edit_file(calc.py) ok" in trace
    assert "tests: 1 passed, 0 failed" in trace
