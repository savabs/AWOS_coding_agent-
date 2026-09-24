"""
A reply cut off by the output limit is not a finish.

Live, DeepSeek v4 Flash spent its whole 4096-token reply on hidden reasoning
after profiling ("empty reply, finish=length"), and the loop read the empty
reply as "done" — on a task a later attempt solved (20/20, 5.18x).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scaffold.agent.agent_loop import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    MAX_TRUNCATIONS,
    AgentLoop,
    AnthropicToolClient,
    ModelReply,
    OpenAIToolClient,
    ToolCall,
    build_coding_registry,
)

CUT_OFF = SimpleNamespace(stop_reason="max_tokens")  # Anthropic's "hit the limit"


class _Scripted(AnthropicToolClient):
    def __init__(self, replies):
        super().__init__(client=None, model="claude-test")
        self._replies = list(replies)
        self.seen = []

    def complete(self, system, messages, registry):
        self.seen.append(messages[-1])
        return self._replies.pop(0) if self._replies else ModelReply(text="done")


def _project():
    root = Path(tempfile.mkdtemp())
    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    return root


def _loop(client, root):
    return AgentLoop(registry=build_coding_registry(str(root), sandbox=None), client=client)


def test_a_cut_off_empty_reply_is_not_a_finish(monkeypatch):
    monkeypatch.setenv("AWOS_SANDBOX", "none")
    root = _project()
    client = _Scripted([
        ModelReply(text="", raw=CUT_OFF, output_tokens=4096),
        ModelReply(tool_calls=[ToolCall(id="t", name="edit_file", arguments={
            "path": "calc.py", "old_string": "a - b", "new_string": "a + b"})]),
        ModelReply(text="Fixed."),
    ])
    outcome = _loop(client, root).run("fix add")

    assert outcome.stop_reason == "completed" and outcome.success
    assert "a + b" in (root / "calc.py").read_text()
    # The model was told it was cut off, not that it was done.
    assert "cut off by the output limit" in str(client.seen[1])


def test_repeated_cut_offs_stop_as_truncated_not_completed(monkeypatch):
    monkeypatch.setenv("AWOS_SANDBOX", "none")
    client = _Scripted([ModelReply(text="", raw=CUT_OFF)] * (MAX_TRUNCATIONS + 1))
    outcome = _loop(client, _project()).run("fix add")

    assert outcome.stop_reason == "truncated"
    assert not outcome.success


def test_a_normal_finish_still_completes(monkeypatch):
    monkeypatch.setenv("AWOS_SANDBOX", "none")
    client = _Scripted([ModelReply(text="Nothing to do.", raw=SimpleNamespace(stop_reason="end_turn"))])
    outcome = _loop(client, _project()).run("explain add")
    assert outcome.stop_reason == "completed"


def test_output_budget_default_and_env(monkeypatch):
    monkeypatch.delenv("AWOS_AGENT_MAX_OUTPUT_TOKENS", raising=False)
    assert OpenAIToolClient(None, "m").max_tokens == DEFAULT_MAX_OUTPUT_TOKENS
    monkeypatch.setenv("AWOS_AGENT_MAX_OUTPUT_TOKENS", "8000")
    assert AnthropicToolClient(None, "m").max_tokens == 8000
    assert OpenAIToolClient(None, "m", max_tokens=123).max_tokens == 123


def test_truncated_stop_is_resumable():
    from scaffold.agent.orchestrator import _agent_resume_reason

    outcome = SimpleNamespace(stop_reason="truncated", final_message="")
    assert "output limit" in _agent_resume_reason(outcome, {"success": False, "test_result": None}, 0)
