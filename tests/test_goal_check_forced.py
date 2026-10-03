"""
The checker's verdict is forced, not requested.

A "decide now" prompt was ignored: 3 of 4 checks in a full run ended UNVERIFIED
on the cost cap or turn limit. Near the limit — and in the one follow-up turn
after a check ends without a verdict — the reply must call submit_verdict.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scaffold.agent.agent_loop import AnthropicToolClient, ModelReply, OpenAIToolClient
from scaffold.agent.goal_check import (
    NUDGEABLE_STOPS, VERDICT_TOOL, _complete_forcing, _EndOnVerdict,
)

REGISTRY = SimpleNamespace(openai_schemas=lambda: [], anthropic_schemas=lambda: [])


class _API:
    """Stands in for client.chat.completions / client.messages; records tool_choice."""

    def __init__(self, shape, sent):
        self.shape, self.sent = shape, sent

    def create(self, **kw):
        self.sent[self.shape] = kw.get("tool_choice")
        if self.shape == "openai":
            msg = SimpleNamespace(content="", tool_calls=[])
            return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")],
                                   usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))
        return SimpleNamespace(content=[], usage=SimpleNamespace(input_tokens=1, output_tokens=1),
                               stop_reason="end_turn")


def test_adapters_send_a_forced_tool_choice():
    sent = {}
    openai = OpenAIToolClient(SimpleNamespace(chat=SimpleNamespace(completions=_API("openai", sent))), "m")
    anthropic = AnthropicToolClient(SimpleNamespace(messages=_API("anthropic", sent)), "m")

    openai.complete("s", [], REGISTRY, tool_choice=VERDICT_TOOL)
    anthropic.complete("s", [], REGISTRY, tool_choice=VERDICT_TOOL)
    assert sent["openai"] == {"type": "function", "function": {"name": VERDICT_TOOL}}
    assert sent["anthropic"] == {"type": "tool", "name": VERDICT_TOOL}

    # Without it nothing is forced, and the request is as before.
    openai.complete("s", [], REGISTRY)
    anthropic.complete("s", [], REGISTRY)
    assert sent == {"openai": None, "anthropic": None}


def test_nearly_out_forces_the_verdict_tool():
    forced = []

    class _Inner:
        model = "m"

        def complete(self, system, messages, registry, tool_choice=None):
            forced.append(tool_choice)
            return ModelReply(text="", input_tokens=1, output_tokens=1)

    gated = _EndOnVerdict(_Inner(), SimpleNamespace(verdict=None), model="m", max_turns=4)
    for _ in range(4):
        gated.complete("SYS", [], None)
    assert forced == [None, None, VERDICT_TOOL, VERDICT_TOOL]


def test_forcing_is_skipped_for_clients_without_tool_choice():
    # Replay cassettes and test doubles take (system, messages, registry) only.
    class _Old:
        def complete(self, system, messages, registry):
            return "ok"

    assert _complete_forcing(_Old(), "s", [], None, VERDICT_TOOL) == "ok"


def test_the_cost_cap_is_never_exceeded_for_a_verdict():
    # The cap is a promise: no nudge and no re-check after it trips. The
    # verdict is forced before it instead (test_nearly_out_forces_the_verdict_tool).
    assert "cost_cap" not in NUDGEABLE_STOPS


def test_the_cost_threshold_forces_the_verdict_before_the_cap():
    forced = []

    class _Inner:
        model = "claude-haiku-4-5"

        def complete(self, system, messages, registry, tool_choice=None):
            forced.append(tool_choice)
            return ModelReply(text="", input_tokens=60_000, output_tokens=0)  # $0.06 a turn

    gated = _EndOnVerdict(_Inner(), SimpleNamespace(verdict=None),
                          model="claude-haiku-4-5", cap=0.20, max_turns=24)
    for _ in range(4):
        gated.complete("SYS", [], None)
    # $0.06, $0.12 spent: under 75% of $0.20. After $0.18 the next reply is forced.
    assert forced == [None, None, None, VERDICT_TOOL]
