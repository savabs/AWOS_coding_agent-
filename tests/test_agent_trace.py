"""
Tests for the action trace in scaffold/agent/agent_loop.py.

A run log that shows only per-task outcomes cannot say why an agent "measured
but made no edit". The trace prints one compact line per tool call, the
model's own words, and why the loop stopped — naming commands and paths, never
file contents or edit strings, and never changing what the model is sent.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scaffold"))

from scaffold.agent.agent_loop import AgentLoop, ModelReply, ToolCall
from scaffold.agent.tools.base import ToolResult

SECRET_OLD = "def total(rows):\n    return sum(r.amount for r in rows)  # OLD-BODY-MARKER"
SECRET_NEW = "def total(rows):\n    return math.fsum(r.amount for r in rows)  # NEW-BODY-MARKER"


class _FakeRegistry:
    """Scripted results per tool name; edit_file fails when told to."""

    def __init__(self, failing=()):
        self.failing = set(failing)

    def execute(self, name, arguments):
        if name in self.failing:
            return ToolResult(success=False, text="", error="old_string not found in ledger/fx.py\nsecond line")
        if name == "read_file":
            return ToolResult(success=True, text="FILE-CONTENT-MARKER " * 50, data={})
        data = {"path": arguments.get("path")} if name == "edit_file" else {}
        return ToolResult(success=True, text=f"{name} ok", data=data)


class _ScriptedClient:
    def __init__(self, replies):
        self._replies = list(replies)
        self.sent = []

    def complete(self, system, messages, registry):
        self.sent.append(repr(messages))
        if not self._replies:
            return ModelReply(text="All done.")
        return self._replies.pop(0)

    def format_assistant_turn(self, reply):
        return {"role": "assistant", "content": reply.text}

    def format_tool_results(self, calls, results):
        return [{"role": "user", "content": f"{c.name}: {r.success}"} for c, r in zip(calls, results)]


def _call(name, args, text=""):
    return ModelReply(text=text, tool_calls=[ToolCall(id="c", name=name, arguments=args)])


def _run(replies, failing=(), **kwargs):
    client = _ScriptedClient(replies)
    loop = AgentLoop(_FakeRegistry(failing), client, max_turns=20, **kwargs)
    return loop.run("task"), client


def _agent_lines(capsys):
    return [l for l in capsys.readouterr().out.splitlines() if l.startswith("[agent] ")]


@pytest.fixture(autouse=True)
def _trace_on(monkeypatch):
    monkeypatch.delenv("AWOS_AGENT_TRACE", raising=False)


def test_ok_call_line_names_command_and_time(capsys):
    _run([_call("run_command", {"command": "python -m ledger demo --size large"})])
    lines = _agent_lines(capsys)
    assert any(
        l.startswith("[agent] t1 run_command(python -m ledger demo --size large) -> ok ")
        and l.endswith("s")
        for l in lines
    ), lines


def test_failed_call_shows_first_error_line_only(capsys):
    _run(
        [_call("edit_file", {"path": "ledger/fx.py", "old_string": SECRET_OLD, "new_string": SECRET_NEW})],
        failing={"edit_file"},
    )
    lines = _agent_lines(capsys)
    assert "[agent] t1 edit_file(ledger/fx.py) -> FAILED: old_string not found in ledger/fx.py" in lines
    assert not any("second line" in l for l in lines)


def test_model_text_is_one_truncated_line(capsys):
    text = "I measured the report.\nIt takes 4.2s. " + "x" * 400
    _run([_call("run_tests", {}, text=text)])
    says = [l for l in _agent_lines(capsys) if l.startswith("[agent] t1 says: ")]
    assert len(says) == 1
    assert "I measured the report. It takes 4.2s." in says[0]
    assert len(says[0]) <= len("[agent] t1 says: ") + 160


def test_stop_line_summarises_run(capsys):
    _run(
        [
            _call("read_file", {"path": "a.py"}),
            _call("edit_file", {"path": "a.py", "old_string": "a", "new_string": "b"}),
            _call("run_tests", {}),
        ],
        failing={"edit_file"},
    )
    lines = _agent_lines(capsys)
    assert lines[-1] == "[agent] stop: completed after 4 turns, 3 tool calls (1 failed), nudges 0"


def test_nudge_is_traced_and_counted(capsys):
    # First finish is empty with no edit: nudged once, then the edit lands.
    _run(
        [
            ModelReply(text="Nothing to change."),
            _call("edit_file", {"path": "a.py", "old_string": "a", "new_string": "b"}),
        ],
        require_edits=True,
    )
    lines = _agent_lines(capsys)
    assert any(l.startswith("[agent] t1 nudged (1/") for l in lines), lines
    assert lines[-1].endswith("nudges 1")


def test_trace_off_prints_nothing(capsys, monkeypatch):
    monkeypatch.setenv("AWOS_AGENT_TRACE", "0")
    _run([_call("run_command", {"command": "ls"}, text="looking")])
    assert _agent_lines(capsys) == []


def test_long_arguments_are_truncated(capsys):
    command = "python -c '" + "print(1);" * 60 + "'"
    _run([_call("run_command", {"command": command})])
    line = next(l for l in _agent_lines(capsys) if "run_command(" in l)
    summary = line.split("run_command(", 1)[1].rsplit(") -> ", 1)[0]
    assert len(summary) <= 100
    assert summary.endswith("...")


def test_no_file_contents_or_edit_strings_in_output(capsys):
    _run(
        [
            _call("read_file", {"path": "ledger/fx.py", "start_line": 10, "end_line": 40}),
            _call("edit_file", {"path": "ledger/fx.py", "old_string": SECRET_OLD, "new_string": SECRET_NEW}),
        ]
    )
    out = capsys.readouterr().out
    assert "[agent] t1 read_file(ledger/fx.py, 10-40) -> ok" in out
    assert "[agent] t2 edit_file(ledger/fx.py) -> ok" in out
    for marker in ("FILE-CONTENT-MARKER", "OLD-BODY-MARKER", "NEW-BODY-MARKER", "fsum"):
        assert marker not in out


@pytest.mark.parametrize("command,secret,kept", [
    ("OPENROUTER_API_KEY=sk-or-v1-abcdef123456 python x.py", "abcdef123456", "python x.py"),
    ("curl -H 'Authorization: Bearer sk-ant-abcdefghijk' https://api", "abcdefghijk", "https://api"),
    ('export GITHUB_TOKEN="ghp_secretvalue"; ls', "ghp_secretvalue", "; ls"),
    ("echo sk-proj-AAAAAAAAAAAAAAAA", "AAAAAAAAAAAAAAAA", "echo sk-"),
    ("DB_PASSWORD=hunter2 ./migrate", "hunter2", "./migrate"),
])
def test_inline_secrets_in_commands_are_masked(capsys, command, secret, kept):
    _run([_call("run_command", {"command": command})])
    line = next(l for l in _agent_lines(capsys) if "run_command(" in l)
    assert secret not in line
    assert "***" in line and kept in line


def test_ordinary_commands_are_traced_unmasked(capsys):
    command = "AWOS_MAX_TURNS=5 pytest -k test_api_key tests/"
    _run([_call("run_command", {"command": command})])
    assert any(command in l for l in _agent_lines(capsys))


def test_trace_does_not_change_messages_sent(capsys, monkeypatch):
    replies = lambda: [
        _call("run_command", {"command": "ls"}, text="look"),
        _call("edit_file", {"path": "a.py", "old_string": "a", "new_string": "b"}),
    ]
    _, traced = _run(replies())
    monkeypatch.setenv("AWOS_AGENT_TRACE", "0")
    _, silent = _run(replies())
    assert traced.sent == silent.sent


def test_empty_reply_reports_tokens_and_finish_reason(capsys):
    class _Choice:
        finish_reason = "length"

    class _Raw:
        choices = [_Choice()]

    _run([ModelReply(text="", output_tokens=4096, raw=_Raw())])
    lines = _agent_lines(capsys)
    assert "[agent] t1 empty reply (4096 output tokens, finish=length)" in lines
