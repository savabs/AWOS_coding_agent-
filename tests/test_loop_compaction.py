"""Tests for scaffold/agent/loop_compaction.py (two-stage loop compaction).

No model or network calls: conversations are built with the real clients'
format_assistant_turn / format_tool_results, and `summarize` is a fake."""

from __future__ import annotations

import random
import threading

import pytest

from scaffold.agent import loop_compaction as lc
from scaffold.agent.agent_loop import (AnthropicToolClient, ModelReply, OpenAIToolClient,
                                       ToolCall)
from scaffold.agent.loop_grammar import GrammarLoopClient
from scaffold.agent.tools.base import ToolResult

GOAL = "Please solve this task.\n<task>\nFix the bug in foo.py\n</task>"
WINDOW = 1000


def _clients():
    openai = OpenAIToolClient(client=None, model="local-model")
    return {
        "anthropic": AnthropicToolClient(client=None, model="claude-haiku-5-5"),
        "openai": openai,
        "text": GrammarLoopClient(openai),
    }


SHAPES = ["anthropic", "openai", "text"]


class Conv:
    """Builds a loop-shaped conversation with a given client."""

    def __init__(self, shape: str, seed: int = 0) -> None:
        self.shape = shape
        self.client = _clients()[shape]
        self.messages = [{"role": "user", "content": GOAL}]
        self.n = 0
        self.rng = random.Random(seed)

    def turn(self, calls: int = 1, text: str = "thinking") -> None:
        if self.shape == "text":
            calls = 1  # the text protocol has exactly one action per reply
        tcs = []
        for _ in range(calls):
            self.n += 1
            tcs.append(ToolCall(id=f"call_{self.n}", name="bash",
                                arguments={"command": f"echo {self.n}"}))
        reply = ModelReply(text=f"{text} {self.n}", tool_calls=tcs)
        self.messages.append(self.client.format_assistant_turn(reply))
        results = [ToolResult(success=True, text=f"<returncode>0</returncode> out {c.id}")
                   for c in tcs]
        self.messages.extend(self.client.format_tool_results(tcs, results))

    def nudge(self) -> None:
        """A format-error round: assistant text with no call, then a user nudge."""
        self.messages.append(self.client.format_assistant_turn(ModelReply(text="hmm")))
        self.messages.append({"role": "user", "content": "Your reply had no bash call."})

    def bare_nudge(self) -> None:
        """Empty reply (not appended) then the nudge: two user messages in a row."""
        self.messages.append({"role": "user", "content": "Your reply had no bash call."})


def _fake(texts=None):
    calls = []

    def summarize(msgs):
        calls.append(msgs)
        return (texts or {}).get(len(calls), f"SUMMARY-{len(calls)}")

    summarize.calls = calls
    return summarize


def _compactor(summarize, **kw):
    kw.setdefault("window", WINDOW)
    kw.setdefault("keep_recent", 3)
    return lc.Compactor("claude-haiku-5-5", summarize=summarize, **kw)


def _assert_valid(new, *, strict=True):
    """Valid for every provider: no orphan results; tail after the goal/summary
    starts with an assistant; strict user/assistant alternation (tool messages
    and tool-result user messages count as the user side)."""
    assert lc.pairs_intact(new)
    assert new[0]["role"] == "user"
    if strict:
        assert new[1]["role"] == "assistant"
        prev = "user"
        for m in new[1:]:
            side = "assistant" if m["role"] == "assistant" else "user"
            if side == "assistant":
                assert prev == "user", "two assistant messages in a row"
            prev = side


# ── config helpers ──────────────────────────────────────────────────────────


def test_enabled_default_off(monkeypatch):
    monkeypatch.delenv("AWOS_LOOP_COMPACT", raising=False)
    assert lc.compaction_enabled() is False
    for v in ("1", "true", "on", "YES"):
        monkeypatch.setenv("AWOS_LOOP_COMPACT", v)
        assert lc.compaction_enabled() is True
    monkeypatch.setenv("AWOS_LOOP_COMPACT", "0")
    assert lc.compaction_enabled() is False


def test_context_window(monkeypatch):
    monkeypatch.delenv("AWOS_LOOP_CONTEXT_TOKENS", raising=False)
    assert lc.context_window("claude-haiku-5-5") == 200_000
    assert lc.context_window("us.anthropic.claude-sonnet-4") == 200_000
    assert lc.context_window("claude-opus-5-5[1m]") == 1_000_000
    assert lc.context_window("qwen3-coder-30b") == 32_768
    assert lc.context_window("") == 32_768
    monkeypatch.setenv("AWOS_LOOP_CONTEXT_TOKENS", "65536")
    assert lc.context_window("claude-haiku-5-5") == 65536
    monkeypatch.setenv("AWOS_LOOP_CONTEXT_TOKENS", "junk")
    assert lc.context_window("x") == 32_768


@pytest.mark.parametrize("shape", SHAPES)
def test_estimate_tokens_all_shapes(shape):
    c = Conv(shape)
    for _ in range(3):
        c.turn(calls=2)
    est = lc.estimate_tokens(c.messages, system="sys")
    chars = sum(len(lc.render_message(m)) for m in c.messages)
    assert est > 0
    assert 0.5 * chars / 4 < est < 3 * chars / 4 + 100
    assert lc.estimate_tokens([]) == 0
    # robust to odd content
    assert lc.estimate_tokens([{"role": "assistant", "content": None},
                               {"role": "user", "content": [object()]}, "junk"]) > 0


def test_summary_request_has_no_tool_blocks():
    c = Conv("anthropic")
    c.turn(calls=2)
    req = lc.summary_request(c.messages[1:])
    assert len(req) == 1 and req[0]["role"] == "user"
    assert isinstance(req[0]["content"], str)
    assert "[tool call bash]" in req[0]["content"]
    assert "out call_1" in req[0]["content"]
    o = Conv("openai")
    o.turn(calls=2)
    text = lc.render_transcript(o.messages[1:])
    assert "[tool call bash]" in text and "### tool result" in text


# ── thresholds ──────────────────────────────────────────────────────────────


def test_nothing_below_thresholds():
    s = _fake()
    comp = _compactor(s)
    c = Conv("anthropic")
    for _ in range(10):
        c.turn()
    comp.maybe_start(c.messages, 699)
    assert comp._pending is None
    assert comp.maybe_swap(c.messages, 849) is None
    assert s.calls == [] and comp.summaries == 0


def test_too_few_turns_no_swap():
    s = _fake()
    comp = _compactor(s, keep_recent=3)
    c = Conv("openai")
    for _ in range(3):
        c.turn()
    comp.maybe_start(c.messages, 900)
    assert comp.maybe_swap(c.messages, 900) is None
    assert s.calls == [] and comp.failures == 0


def test_zero_used_tokens_falls_back_to_estimate():
    s = _fake()
    comp = _compactor(s, window=10_000_000)
    c = Conv("text")
    for _ in range(8):
        c.turn()
    assert comp.maybe_swap(c.messages, 0) is None
    comp2 = _compactor(_fake(), window=lc.estimate_tokens(c.messages))
    assert comp2.maybe_swap(c.messages, 0) is not None


# ── the swap, all shapes ────────────────────────────────────────────────────


@pytest.mark.parametrize("shape", SHAPES)
def test_background_then_swap(shape):
    s = _fake()
    comp = _compactor(s, keep_recent=3)
    comp.note_files(["foo.py", "tests/test_foo.py"])
    c = Conv(shape)
    for i in range(10):
        c.turn(calls=1 + i % 3)
    comp.maybe_start(c.messages, 720)
    assert comp.background_runs == 1
    comp._pending["thread"].join(5)
    cut = comp._pending["cut"]
    # turns appended while the summary ran stay verbatim
    c.turn()
    c.turn()
    comp.note_files(["bar.py", "foo.py"])
    new = comp.maybe_swap(c.messages, 900)
    assert new is not None
    assert comp.sync_runs == 0 and comp.summaries == 1 and len(s.calls) == 1
    # goal verbatim as the prefix of the first message
    assert new[0]["content"].startswith(GOAL)
    body = new[0]["content"]
    assert "SUMMARY-1" in body and "summary number 1 of earlier work" in body
    assert "Files edited so far: foo.py, tests/test_foo.py, bar.py" in body
    # tail verbatim (same objects), including the turns appended after start
    assert new[1:] == c.messages[cut:]
    assert all(a is b for a, b in zip(new[1:], c.messages[cut:]))
    assert new[-1] is c.messages[-1]
    # the last keep_recent assistant turns are kept (at least)
    n_assist = sum(1 for m in new if m["role"] == "assistant")
    assert n_assist >= 3 + 2
    _assert_valid(new)
    # summarize saw exactly the dropped part (no goal)
    assert s.calls[0] == c.messages[1:cut]
    t = comp.summary()
    assert t["summaries"] == 1 and t["last_cut"] == cut
    assert t["tokens_before"] == 900 and t["tokens_after"] == lc.estimate_tokens(new)
    assert t["tokens_after"] < lc.estimate_tokens(c.messages)


@pytest.mark.parametrize("shape", SHAPES)
def test_sync_fallback_when_not_started(shape):
    s = _fake()
    comp = _compactor(s)
    c = Conv(shape)
    for _ in range(8):
        c.turn(calls=2)
    new = comp.maybe_swap(c.messages, 950)
    assert new is not None
    assert comp.sync_runs == 1 and comp.background_runs == 0
    _assert_valid(new)


def test_background_not_ready_waits_or_retries():
    gate = threading.Event()
    calls = []

    def slow(msgs):
        calls.append(1)
        gate.wait(5)
        return "SLOW"

    comp = _compactor(slow, wait_s=0.05)
    c = Conv("anthropic")
    for _ in range(8):
        c.turn()
    comp.maybe_start(c.messages, 750)
    assert comp.maybe_swap(c.messages, 900) is None  # timed out waiting, no failure
    assert comp.failures == 0 and comp.summaries == 0
    c.turn()
    gate.set()
    new = comp.maybe_swap(c.messages, 950)
    assert new is not None and "SLOW" in new[0]["content"]
    assert len(calls) == 1 and comp.sync_runs == 0


def test_background_default_waits_until_done():
    gate = threading.Event()

    def slow(msgs):
        gate.wait(5)
        return "DONE"

    comp = _compactor(slow)
    c = Conv("openai")
    for _ in range(8):
        c.turn()
    comp.maybe_start(c.messages, 750)
    threading.Timer(0.05, gate.set).start()
    new = comp.maybe_swap(c.messages, 900)
    assert new is not None and "DONE" in new[0]["content"]


def test_start_only_once():
    s = _fake()
    comp = _compactor(s)
    c = Conv("text")
    for _ in range(8):
        c.turn()
    comp.maybe_start(c.messages, 750)
    c.turn()
    comp.maybe_start(c.messages, 800)
    assert comp.background_runs == 1


def test_no_action_right_after_swap():
    s = _fake()
    comp = _compactor(s)
    c = Conv("anthropic")
    for _ in range(10):
        c.turn()
    new = comp.maybe_swap(c.messages, 900)
    assert new is not None
    # same step's (stale) used_tokens: nothing happens on the new list
    comp.maybe_start(new, 900)
    assert comp.maybe_swap(new, 900) is None
    assert len(s.calls) == 1


def test_rewritten_list_discards_pending():
    s = _fake()
    comp = _compactor(s)
    c = Conv("openai")
    for _ in range(10):
        c.turn()
    comp.maybe_start(c.messages, 750)
    comp._pending["thread"].join(5)
    rewritten = [dict(m) for m in c.messages]  # different objects
    new = comp.maybe_swap(rewritten, 900)
    assert new is not None and comp.sync_runs == 1
    _assert_valid(new)


# ── tool pairs ──────────────────────────────────────────────────────────────


SWAPS = {"swap": 0, "none": 0}


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("seed", range(12))
def test_cut_never_splits_tool_pairs(shape, seed):
    rng = random.Random(seed)
    c = Conv(shape, seed)
    for _ in range(rng.randint(4, 20)):
        r = rng.random()
        if r < 0.15:
            c.nudge()
        elif r < 0.22:
            c.bare_nudge()
        else:
            c.turn(calls=rng.randint(1, 4))
    keep = rng.randint(1, 5)
    comp = _compactor(_fake(), keep_recent=keep)
    new = comp.maybe_swap(c.messages, 900)
    if new is None:
        SWAPS["none"] += 1
        return
    SWAPS["swap"] += 1
    cut = comp.last_cut
    assert c.messages[cut]["role"] == "assistant"
    _assert_valid(new)
    # every tool call in the tail has all its results in the tail
    calls_in_tail = set().union(*(lc._tool_use_ids(m) for m in new))
    results_in_tail = set().union(*(lc._tool_result_ids(m) for m in new))
    assert results_in_tail <= calls_in_tail
    assert sum(1 for m in new[1:] if m["role"] == "assistant") >= keep


def test_property_test_exercised_swaps():
    assert SWAPS["swap"] >= 20, SWAPS


def test_cut_moves_earlier_past_a_late_result():
    """An (unusual) result that arrives after the next assistant turn: the cut
    is moved earlier instead of orphaning it."""
    c = Conv("anthropic")
    for _ in range(6):
        c.turn()
    # assistant A calls x, assistant B (text), then x's result
    c.messages.append({"role": "assistant", "content": [
        {"type": "tool_use", "id": "x", "name": "bash", "input": {"command": "ls"}}]})
    c.messages.append({"role": "user", "content": "interjection"})
    c.messages.append({"role": "assistant", "content": [{"type": "text", "text": "B"}]})
    c.messages.append({"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "x", "content": "late"}]})
    c.turn()
    comp = _compactor(_fake(), keep_recent=2)
    new = comp.maybe_swap(c.messages, 900)
    assert new is not None
    assert lc.pairs_intact(new)
    assert any(lc._tool_use_ids(m) == {"x"} for m in new)


def test_pairs_intact_detects_orphans():
    assert lc.pairs_intact([{"role": "user", "content": "g"}])
    assert not lc.pairs_intact([{"role": "user", "content": "g"},
                                {"role": "tool", "tool_call_id": "a", "content": "r"}])
    assert not lc.pairs_intact([{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "a", "content": "r"}]}])


# ── failure path ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["raise", "empty", "none"])
def test_failure_returns_none_and_backs_off(bad):
    calls = []

    def summarize(msgs):
        calls.append(1)
        if bad == "raise":
            raise RuntimeError("model down")
        return "" if bad == "empty" else None

    comp = _compactor(summarize, keep_recent=2)
    c = Conv("anthropic")
    for _ in range(8):
        c.turn()
    assert comp.maybe_swap(c.messages, 900) is None
    assert comp.failures == 1 and len(calls) == 1
    # next steps: no retry every step
    for _ in range(1):
        c.turn()
        comp.maybe_start(c.messages, 900)
        assert comp.maybe_swap(c.messages, 900) is None
    assert len(calls) == 1
    # after a few more turns, one retry
    c.turn()
    c.turn()
    assert comp.maybe_swap(c.messages, 900) is None
    assert len(calls) == 2 and comp.failures == 2
    # MAX_FAILURES reached: never again
    for _ in range(10):
        c.turn()
    comp.maybe_start(c.messages, 990)
    assert comp.maybe_swap(c.messages, 990) is None
    assert len(calls) == 2


def test_background_failure_no_immediate_sync_retry():
    calls = []

    def summarize(msgs):
        calls.append(1)
        raise RuntimeError("boom")

    comp = _compactor(summarize)
    c = Conv("openai")
    for _ in range(8):
        c.turn()
    comp.maybe_start(c.messages, 750)
    comp._pending["thread"].join(5)
    assert comp.maybe_swap(c.messages, 900) is None
    assert len(calls) == 1 and comp.failures == 1
    assert comp.summary()["failures"] == 1


def test_base_exceptions():
    """In the background thread anything counts as a failure; on the sync path
    a KeyboardInterrupt still reaches the caller."""
    def summarize(msgs):
        raise SystemExit(3)

    comp = _compactor(summarize)
    c = Conv("text")
    for _ in range(8):
        c.turn()
    comp.maybe_start(c.messages, 750)
    comp._pending["thread"].join(5)
    assert comp.maybe_swap(c.messages, 900) is None
    assert comp.failures == 1

    def interrupted(msgs):
        raise KeyboardInterrupt

    comp2 = _compactor(interrupted)
    with pytest.raises(KeyboardInterrupt):
        comp2.maybe_swap(c.messages, 900)


# ── multiple compactions ────────────────────────────────────────────────────


@pytest.mark.parametrize("shape", SHAPES)
def test_multiple_compactions(shape):
    s = _fake()
    comp = _compactor(s, keep_recent=2)
    comp.note_files(["a.py"])
    c = Conv(shape)
    for _ in range(6):
        c.turn()
    first = comp.maybe_swap(c.messages, 900)
    assert first is not None and comp.summaries == 1
    msgs = list(first)
    conv = Conv(shape)
    conv.messages = msgs
    conv.n = 100
    for _ in range(6):
        conv.turn()
    comp.note_files(["b.py"])
    comp.maybe_start(msgs, 760)
    comp._pending["thread"].join(5)
    second = comp.maybe_swap(msgs, 900)
    assert second is not None and comp.summaries == 2
    # the earlier summary is part of what got summarised
    head2 = s.calls[1]
    assert head2[0]["role"] == "user" and "SUMMARY-1" in head2[0]["content"]
    assert "summary number 1" in head2[0]["content"]
    assert GOAL not in head2[0]["content"]
    # goal verbatim, only the latest summary in the first message
    body = second[0]["content"]
    assert body.startswith(GOAL) and body.count(GOAL) == 1
    assert "SUMMARY-2" in body and "summary number 2" in body
    assert "SUMMARY-1" not in body
    assert "Files edited so far: a.py, b.py" in body
    _assert_valid(second)
    assert len(comp.summary()["history"]) == 2


def test_separate_summary_message_mode():
    s = _fake()
    comp = _compactor(s, keep_recent=2, merge_summary=False)
    c = Conv("anthropic")
    for _ in range(6):
        c.turn()
    new = comp.maybe_swap(c.messages, 900)
    assert new[0] == {"role": "user", "content": GOAL}
    assert new[1]["role"] == "user" and "summary number 1" in new[1]["content"]
    assert new[2]["role"] == "assistant"
    assert lc.pairs_intact(new)
    conv = Conv("anthropic")
    conv.messages = list(new)
    conv.n = 100
    for _ in range(5):
        conv.turn()
    second = comp.maybe_swap(conv.messages, 900)
    assert second is not None
    assert "SUMMARY-1" in s.calls[1][0]["content"]
    assert s.calls[1][1]["role"] == "assistant"
    assert second[0] == {"role": "user", "content": GOAL}
    assert "summary number 2" in second[1]["content"]
    assert second[2]["role"] == "assistant"


def test_goal_with_block_content_is_merged():
    s = _fake()
    comp = _compactor(s, keep_recent=2)
    c = Conv("anthropic")
    c.messages[0] = {"role": "user", "content": [{"type": "text", "text": GOAL}]}
    for _ in range(6):
        c.turn()
    new = comp.maybe_swap(c.messages, 900)
    assert new[0]["content"][0] == {"type": "text", "text": GOAL}
    assert "SUMMARY-1" in new[0]["content"][-1]["text"]
    assert c.messages[0]["content"] == [{"type": "text", "text": GOAL}]  # not mutated


def test_invalid_fractions():
    with pytest.raises(ValueError):
        lc.Compactor("m", start_frac=0.9, swap_frac=0.8, summarize=_fake())


# ── robustness: next-request trigger, reserve, budget, fallback, force ──────


def test_next_request_tokens():
    assert lc.next_request_tokens(0, 50, []) == 0  # unknown input: caller estimates
    assert lc.next_request_tokens(1000, 200, []) == 1200
    appended = [{"role": "user", "content": "x" * 400}]
    assert lc.next_request_tokens(1000, 200, appended) == 1200 + lc.estimate_tokens(appended)


def test_threshold_keeps_a_reserve_and_the_stage_ratio():
    comp = _compactor(_fake(), window=32_768, reserve_tokens=11_692)
    assert comp.threshold(comp.swap_frac) == 32_768 - 11_692
    assert comp.threshold(comp.start_frac) == pytest.approx(
        (32_768 - 11_692) * comp.start_frac / comp.swap_frac)
    big = _compactor(_fake(), window=200_000, reserve_tokens=11_692)
    assert big.threshold(big.swap_frac) == 0.85 * 200_000  # the fraction binds first
    tiny = _compactor(_fake(), window=1000, reserve_tokens=50_000)
    assert tiny.reserve_tokens == 500 and tiny.threshold(tiny.swap_frac) == 500
    assert _compactor(_fake()).threshold(0.85) == 0.85 * WINDOW  # no reserve: as before


def test_reserve_makes_the_swap_happen_earlier():
    conv = Conv("anthropic")
    for _ in range(6):
        conv.turn()
    plain, reserved = _compactor(_fake()), _compactor(_fake(), reserve_tokens=400)
    assert plain.maybe_swap(conv.messages, 700) is None
    assert reserved.maybe_swap(conv.messages, 700) is not None


def test_summary_request_budget_is_fair_and_keeps_commands():
    msgs = [{"role": "user", "content": "short goal"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "running the tests"},
                {"type": "tool_use", "id": "t1", "name": "bash",
                 "input": {"command": "pytest -x tests/test_big.py"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                          "content": "<returncode>1</returncode>\n<output>\n"
                                          + "noise line\n" * 5000 + "FAILED test_x\n</output>"}]},
            {"role": "assistant", "content": "a small reply"}]
    full = lc.summary_request(msgs)[0]["content"]
    cut = lc.summary_request(msgs, budget_chars=3000)[0]["content"]
    assert len(full) > 50_000
    body = cut.split("<transcript>\n", 1)[1]
    assert len(body) <= 3000 + 200
    assert "short goal" in body and "a small reply" in body  # short messages intact
    assert "pytest -x tests/test_big.py" in body               # the command
    assert "<returncode>1</returncode>" in body                # its exit code
    assert "FAILED test_x" in body                             # the tail of the output
    assert "characters cut from the middle" in body


def test_fair_caps_water_filling():
    assert lc._fair_caps([10, 20, 30], 100) == [10, 20, 30]
    caps = lc._fair_caps([10, 1000, 2000], 610)
    assert caps[0] == 10 and caps[1] == caps[2] == 300 and sum(caps) <= 610


@pytest.mark.parametrize("shape", SHAPES)
def test_deterministic_summary_lists_commands_codes_and_last_error(shape):
    conv = Conv(shape)
    bad = "<returncode>2</returncode>\n<output>\nTraceback\nKeyError: 'x'\n</output>"
    if shape == "text":  # the text protocol's replies carry a fenced bash block
        conv.messages += [
            {"role": "assistant", "content": "look\n```bash\necho 1\n```"},
            {"role": "user", "content": "<returncode>0</returncode>\n<output>\n1\n</output>"},
            {"role": "assistant", "content": "try\n```bash\npython run.py\n```"},
            {"role": "user", "content": bad}]
    else:
        conv.turn()
        tc = ToolCall(id="bad1", name="bash", arguments={"command": "python run.py"})
        conv.messages.append(conv.client.format_assistant_turn(
            ModelReply(text="try", tool_calls=[tc])))
        conv.messages.extend(conv.client.format_tool_results(
            [tc], [ToolResult(success=False, text=bad, error=bad)]))
    text = lc.deterministic_summary(conv.messages, ["run.py"])
    assert "`echo 1` -> exit 0" in text
    assert "`python run.py` -> exit 2" in text
    assert "KeyError: 'x'" in text and "run.py" in text


def test_deterministic_summary_never_raises():
    assert lc.deterministic_summary([None, 3, {"role": "assistant", "content": object()}])
    assert "No commands" in lc.deterministic_summary([])


@pytest.mark.parametrize("bad", [RuntimeError("down"), ""])
def test_failed_summary_falls_back_to_the_deterministic_one(bad):
    def summarize(msgs):
        if isinstance(bad, Exception):
            raise bad
        return bad

    conv = Conv("anthropic")
    for _ in range(6):
        conv.turn()
    comp = _compactor(summarize, deterministic_fallback=True)
    new = comp.maybe_swap(conv.messages, 900)
    assert new is not None and comp.fallbacks == 1 and comp.failures == 0
    assert "Automatic summary" in new[0]["content"] and "`echo 1` -> exit" in new[0]["content"]
    assert comp.history[-1]["fallback"] is True
    _assert_valid(new)


def test_gate_feedback_is_kept_verbatim_next_to_the_goal():
    conv = Conv("anthropic")
    conv.turn()
    feedback = "You submitted, but tests fail:\nFAILED tests/test_a.py::test_x - assert 1 == 2"
    conv.messages.append(conv.client.format_assistant_turn(ModelReply(text="done?")))
    conv.messages.append({"role": "user", "content": feedback})
    for _ in range(5):
        conv.turn()
    comp = _compactor(_fake())
    comp.note_gate_feedback(feedback)
    new = comp.maybe_swap(conv.messages, 900)
    assert new is not None and feedback in new[0]["content"]
    assert new[0]["content"].startswith(GOAL)


def test_gate_feedback_not_duplicated_while_still_in_the_tail():
    conv = Conv("anthropic")
    for _ in range(5):
        conv.turn()
    feedback = "FAILED tests/test_a.py::test_x"
    conv.messages.append(conv.client.format_assistant_turn(ModelReply(text="done?")))
    conv.messages.append({"role": "user", "content": feedback})
    comp = _compactor(_fake())
    comp.note_gate_feedback(feedback)
    new = comp.maybe_swap(conv.messages, 900)
    assert new is not None and feedback not in new[0]["content"]
    assert any(m.get("content") == feedback for m in new[1:])


@pytest.mark.parametrize("shape", SHAPES)
def test_force_swaps_below_the_thresholds(shape):
    conv = Conv(shape)
    for _ in range(3):
        conv.turn()
    s = _fake()
    comp = _compactor(s, keep_recent=6)  # too few turns for keep_recent=6
    assert comp.maybe_swap(conv.messages, 1) is None
    new = comp.force(conv.messages)
    assert new is not None and comp.forced == 1 and len(new) < len(conv.messages)
    _assert_valid(new)


def test_force_with_nothing_to_summarise_returns_none():
    comp = _compactor(_fake())
    assert comp.force([{"role": "user", "content": GOAL}]) is None


def test_restore_continues_the_summary_numbering_on_the_original_goal():
    conv = Conv("anthropic")
    for _ in range(6):
        conv.turn()
    first = _compactor(_fake())
    swapped = first.maybe_swap(conv.messages, 900)
    assert swapped is not None and first.summaries == 1
    # a resumed run: a new compactor over the saved (already compacted) list
    resumed = _compactor(_fake({1: "SECOND"}))
    resumed.restore(swapped, 1)
    assert resumed.summaries == 1 and resumed._goal["content"] == GOAL
    more = list(swapped)
    conv.messages = more
    for _ in range(6):
        conv.turn()
    again = resumed.maybe_swap(conv.messages, 900)
    assert again is not None and resumed.summaries == 2
    content = again[0]["content"]
    assert content.startswith(GOAL) and content.count("<summary number=") == 1
    assert '<summary number="2">' in content and "SECOND" in content
