"""Prompt-cache breakpoints on direct Anthropic calls (2026-10-11)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for p in (REPO, REPO / "scaffold", REPO / "scaffold" / "agent"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from scaffold.agent.agent_loop import with_cache_breakpoints  # noqa: E402

EPH = {"type": "ephemeral"}


def test_marks_tools_system_and_last_message():
    tools = [{"name": "a"}, {"name": "b"}]
    msgs = [{"role": "user", "content": "goal"},
            {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "x"}]}]
    system, out, tl = with_cache_breakpoints("sys", msgs, tools)
    assert system == [{"type": "text", "text": "sys", "cache_control": EPH}]
    assert tl[-1]["cache_control"] == EPH and "cache_control" not in tl[0]
    assert out[-1]["content"][-1]["cache_control"] == EPH
    assert "cache_control" not in str(out[:-1])
    # the caller's objects are untouched
    assert "cache_control" not in tools[-1]
    assert "cache_control" not in msgs[-1]["content"][-1]


def test_string_last_message_becomes_a_block():
    _, out, _ = with_cache_breakpoints("s", [{"role": "user", "content": "hi"}], [])
    assert out[0]["content"] == [{"type": "text", "text": "hi", "cache_control": EPH}]


def test_off_switch(monkeypatch):
    monkeypatch.setenv("AWOS_ANTHROPIC_CACHE", "0")
    msgs = [{"role": "user", "content": "hi"}]
    assert with_cache_breakpoints("s", msgs, [{"name": "a"}]) == ("s", msgs, [{"name": "a"}])
