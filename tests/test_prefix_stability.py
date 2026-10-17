"""
B2 — byte-stable request prefixes for the loop executor. Two consecutive
steps' requests must be identical up to the appended suffix, for the
grammar text protocol (local), the tool-call path, and Anthropic; and the
static part of the prompt must be the same bytes across tasks. Fakes only.
"""
from __future__ import annotations

import json
import platform
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import loop_request_capture as cap  # noqa: E402
from scaffold.agent import loop_executor as lx  # noqa: E402
from scaffold.agent import loop_grammar as lg  # noqa: E402
from scaffold.agent.agent_loop import OpenAIToolClient  # noqa: E402
from test_loop_grammar import FakeLlamaServer  # noqa: E402

MARK = lx.SUBMIT_MARKER


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("AWOS_LOOP_GRAMMAR", "AWOS_PROVIDER", "AWOS_AGENT_MODEL", "AWOS_LOOP_BREAKER",
              "AWOS_ANTHROPIC_CACHE", "AWOS_REASONING_AGENT"):
        monkeypatch.delenv(k, raising=False)


def render_chatml(messages: list) -> str:
    """ChatML as Qwen's template lays it out for plain-text turns (no tools)."""
    return "".join(f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>\n" for m in messages)


def _static(call: dict) -> str:
    """Every request field except the message list, serialised."""
    return json.dumps({k: v for k, v in call.items() if k != "messages"}, sort_keys=True)


def _grammar_calls(goal: str, replies: list) -> list:
    sdk = FakeLlamaServer(list(replies))
    with patch.object(platform, "uname", return_value=cap.UNAME), \
            tempfile.TemporaryDirectory() as ws:
        lx.LoopExecutor(OpenAIToolClient(sdk, "local/qwen3.5-9b", max_tokens=2048),
                        lx.build_loop_registry(cap.FixedSandbox(ws)), goal, workspace=ws,
                        gate=lambda f, s: {"green": True}).run()
    return sdk.calls


REPLIES = ["Look first.\n```bash\ncat calc.py\n```",
           "not a valid reply",                       # format-error nudge path
           "Fix it.\n```bash\nsed -i 's/-/+/' calc.py\n```",
           f"{MARK}\nFixed."]


def test_grammar_steps_are_append_only_and_render_as_prefixes(monkeypatch):
    monkeypatch.setenv("AWOS_PROVIDER", "local")
    calls = _grammar_calls(cap.GOAL, REPLIES)
    assert len(calls) == 4
    for k in range(len(calls) - 1):
        a, b = calls[k], calls[k + 1]
        assert _static(a) == _static(b)                       # model, grammar, max_tokens
        assert b["messages"][:len(a["messages"])] == a["messages"]
        # The rendered prompt of step k, plus the reply the model generated,
        # is a byte prefix of step k+1's rendered prompt: the server's KV for
        # all of it is reusable.
        generated = render_chatml(a["messages"]) + "<|im_start|>assistant\n" + REPLIES[k]
        assert render_chatml(b["messages"]).startswith(generated)


def test_grammar_static_prefix_is_identical_across_tasks(monkeypatch):
    monkeypatch.setenv("AWOS_PROVIDER", "local")
    one = _grammar_calls("Fix add() in calc.py.", [f"{MARK}\nx"])[0]
    two = _grammar_calls("Rename helper() to util() everywhere.", [f"{MARK}\nx"])[0]
    assert _static(one) == _static(two)
    assert one["messages"][0] == two["messages"][0]           # system
    u1, u2 = one["messages"][1]["content"], two["messages"][1]["content"]
    head = u1[:u1.index("<task>\n")]
    # Everything but the task itself is shared: the goal comes last.
    assert u2.startswith(head) and len(u1) - len(head) < 60
    assert u1.endswith("<task>\nFix add() in calc.py.\n</task>\n")


def test_instance_prompts_are_deterministic():
    u = cap.UNAME
    for text in (False, True):
        assert lx.instance_prompt("g", u, text) == lx.instance_prompt("g", u, text)
    assert lg.build_grammar() == lg.build_grammar() == lg.GRAMMAR


def test_tool_path_steps_are_append_only(monkeypatch):
    monkeypatch.setenv("AWOS_LOOP_GRAMMAR", "0")
    calls = cap.capture_openai_tools()
    for a, b in zip(calls, calls[1:]):
        assert _static(a) == _static(b)                       # tools schema bytes too
        assert b["messages"][:len(a["messages"])] == a["messages"]


def _anthropic_norm(messages: list) -> list:
    """Anthropic treats string content as one text block, and cache_control
    markers are not part of the cached content: normalise both away."""
    out = []
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        content = [{k: v for k, v in b.items() if k != "cache_control"} for b in content]
        out.append({"role": m["role"], "content": content})
    return out


def test_anthropic_steps_are_append_only():
    calls = cap.capture_anthropic()
    for a, b in zip(calls, calls[1:]):
        assert a["system"] == b["system"] and a["tools"] == b["tools"]
        na, nb = _anthropic_norm(a["messages"]), _anthropic_norm(b["messages"])
        assert nb[:len(na)] == na


def test_history_is_never_mutated_after_append(monkeypatch):
    """The executor's own message list: every message object, once sent, is
    byte-identical in every later request (no re-render, no rewrite)."""
    monkeypatch.setenv("AWOS_PROVIDER", "local")
    calls = _grammar_calls(cap.GOAL, REPLIES)
    first_seen: dict = {}
    for call in calls:
        for i, m in enumerate(call["messages"]):
            blob = json.dumps(m)
            assert first_seen.setdefault(i, blob) == blob
