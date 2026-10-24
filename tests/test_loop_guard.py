"""Loop breaker (scaffold/agent/loop_guard.py, docs/specs/loop_breaker.md)."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scaffold" / "agent"))

import loop_guard  # noqa: E402
from loop_guard import LoopDetector, detect, guarded_create, truncate  # noqa: E402


# ── detector ─────────────────────────────────────────────────────────────────

def test_flags_repeated_lines_and_cuts_after_first_copy():
    text = "I will fix the bug.\nLet me check the parser.\n" + "Let me check the parser.\n" * 5
    trip = detect(text)
    assert trip and trip.kind == "repeated_lines"
    assert truncate(text, trip) == "I will fix the bug.\nLet me check the parser.\n"


def test_blank_lines_between_repeats_still_count():
    text = "start\n" + "Wait, the index is off by one.\n\n" * 4
    trip = detect(text)
    assert trip and trip.kind == "repeated_lines"


def test_short_closing_lines_are_not_a_loop():
    text = "```js\n" + "".join(" " * (8 - i) + "}\n" for i in range(5)) + "}\n}\n}\n}\n```\n"
    assert detect(text) is None


def test_flags_long_self_match_and_truncates_to_one_copy():
    intro = "Looking at the code, the issue is in get_real_name.\n"
    phrase = ("so the function returns the second token instead of the last one, "
              "which means we need to walk all the dots and ")
    text = intro + phrase * 8
    trip = detect(text)
    assert trip and trip.kind == "self_match"
    kept = truncate(text, trip)
    assert kept.startswith(intro)
    assert len(kept) <= len(intro) + 2 * len(phrase)
    assert phrase.strip() in kept


def test_newline_free_runaway_is_caught():
    text = "Answer: " + "abc def ghi " * 400
    trip = detect(text)
    assert trip and trip.kind == "self_match"
    assert len(truncate(text, trip)) < 100


def _block(path, search, replace):
    return f"{path}\n```python\n<<<<<<< SEARCH\n{search}\n=======\n{replace}\n>>>>>>> REPLACE\n```\n"


def test_long_verbatim_search_block_is_not_flagged():
    # A SEARCH copied verbatim from a file with repeated structure, and a
    # REPLACE that copies most of it again: legitimate, must not trip.
    body = "\n".join(f"    def method_{i % 3}(self):\n        return self._x\n" for i in range(40))
    dup = "\n".join(["    pass"] * 12)          # 12 identical lines inside SEARCH
    reply = ("I'll update the class.\n\n" + _block("pkg/mod.py", body + "\n" + dup, body + "\n    return 1")
             + "\nDone.\n")
    assert detect(reply) is None


def test_many_blocks_with_same_preamble_are_not_flagged():
    reply = "".join(_block("pkg/mod.py", f"x = {i}", f"x = {i + 1}") for i in range(30))
    assert detect(reply) is None


def test_identical_blocks_repeated_trip():
    b = _block("pkg/mod.py", "x = 1", "x = 2")
    reply = "Fix:\n" + b * 4
    trip = detect(reply)
    assert trip and trip.kind == "repeated_block"
    assert parse_count(truncate(reply, trip)) == 1


def parse_count(text):
    from one_shot import parse_blocks
    return len(parse_blocks(text)[0])


def test_normal_varied_text_is_not_flagged():
    text = "\n".join(
        f"Step {i}: inspect {w} and confirm that the {w} handler returns {i * 7} for input {i}."
        for i, w in enumerate(["parser", "lexer", "token", "grouping", "filter", "stack",
                               "formatter", "reindent", "aligned", "keywords"] * 3))
    assert detect(text) is None


def test_incremental_matches_post_hoc():
    text = "a line\n" + "same same same line\n" * 6
    d = LoopDetector()
    trip = None
    for i in range(0, len(text), 7):
        trip = d.feed(text[i:i + 7]) or trip
    assert trip and trip.kind == detect(text).kind and trip.cut == detect(text).cut


# ── guarded call with a fake streaming client ───────────────────────────────

class _Stream:
    def __init__(self, pieces, usage=None):
        self.pieces, self.usage, self.closed, self.served = pieces, usage, False, 0

    def __iter__(self):
        for p in self.pieces:
            self.served += 1
            yield SimpleNamespace(model="m", choices=[SimpleNamespace(
                delta=SimpleNamespace(content=p), finish_reason=None)], usage=None)
        yield SimpleNamespace(model="m", choices=[SimpleNamespace(
            delta=SimpleNamespace(content=None), finish_reason="stop")], usage=None)
        yield SimpleNamespace(model="m", choices=[], usage=self.usage)

    def close(self):
        self.closed = True


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.streams = []

    def create(self, **kw):
        self.calls.append(kw)
        text = self.replies.pop(0)
        if kw.get("stream"):
            pieces = [text[i:i + 16] for i in range(0, len(text), 16)]
            s = _Stream(pieces, SimpleNamespace(prompt_tokens=100, completion_tokens=50))
            self.streams.append(s)
            return s
        return SimpleNamespace(model="m", choices=[SimpleNamespace(
            message=SimpleNamespace(content=text, tool_calls=None), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50))


LOOPING = "Plan: edit sql.py.\n" + "We need to find the last dot.\n" * 400
CLEAN = "sqlparse/sql.py\n" + "<<<<<<< SEARCH\na = 1\n=======\na = 2\n>>>>>>> REPLACE\n"


def test_stream_abort_truncate_and_single_resample(capsys):
    fake = FakeClient([LOOPING, CLEAN])
    resp = guarded_create(fake.create, {"model": "m", "messages": [{"role": "user", "content": "x"}],
                                        "temperature": 0}, component="test")
    assert len(fake.calls) == 2
    assert fake.calls[0]["stream"] is True
    assert fake.streams[0].closed                       # aborted early
    assert fake.streams[0].served < len(fake.streams[0].pieces) / 10
    assert fake.calls[1]["temperature"] == pytest.approx(0.3)
    assert resp.choices[0].message.content == CLEAN
    assert resp.loop_guard == "resampled"
    assert resp.usage.completion_tokens > 50            # the aborted call is counted
    assert "[LOOP-GUARD] tripped: repeated_lines" in capsys.readouterr().err


def test_retry_also_loops_keeps_trimmed_text_as_length():
    fake = FakeClient([LOOPING, LOOPING])
    resp = guarded_create(fake.create, {"model": "m", "messages": []})
    assert len(fake.calls) == 2                          # once, never more
    assert resp.loop_guard == "tripped"
    assert resp.choices[0].finish_reason == "length"
    assert resp.choices[0].message.content == "Plan: edit sql.py.\nWe need to find the last dot.\n"


def test_clean_stream_is_rebuilt_without_flag():
    fake = FakeClient([CLEAN])
    resp = guarded_create(fake.create, {"model": "m", "messages": []})
    assert len(fake.calls) == 1
    assert resp.choices[0].message.content == CLEAN
    assert resp.choices[0].finish_reason == "stop"
    assert resp.loop_guard is None
    assert resp.usage.prompt_tokens == 100 and resp.usage.completion_tokens == 50


class NoStreamClient(FakeClient):
    def create(self, **kw):
        if "stream" in kw:
            raise TypeError("unexpected keyword argument 'stream'")
        return super().create(**kw)


def test_post_hoc_fallback_when_backend_cannot_stream():
    fake = NoStreamClient([LOOPING, CLEAN])
    resp = guarded_create(fake.create, {"model": "m", "messages": []})
    assert [("stream" in c) for c in fake.calls] == [False, False]
    assert resp.loop_guard == "resampled"
    assert resp.choices[0].message.content == CLEAN


def test_tool_call_deltas_are_assembled():
    def create(**kw):
        chunks = [
            SimpleNamespace(model="m", choices=[SimpleNamespace(finish_reason=None, delta=SimpleNamespace(
                content=None, tool_calls=[SimpleNamespace(index=0, id="c1", function=SimpleNamespace(
                    name="edit_file", arguments='{"path": '))]))], usage=None),
            SimpleNamespace(model="m", choices=[SimpleNamespace(finish_reason="tool_calls", delta=SimpleNamespace(
                content=None, tool_calls=[SimpleNamespace(index=0, id=None, function=SimpleNamespace(
                    name=None, arguments='"a.py"}'))]))], usage=None),
        ]
        return iter(chunks)
    resp = guarded_create(create, {"model": "m", "messages": []})
    tc = resp.choices[0].message.tool_calls[0]
    assert (tc.id, tc.function.name, tc.function.arguments) == ("c1", "edit_file", '{"path": "a.py"}')
    assert resp.choices[0].finish_reason == "tool_calls"


# ── wiring: default off is byte-identical ───────────────────────────────────

def test_one_shot_tap_default_off_passes_through(monkeypatch):
    from one_shot import _UsageTap
    monkeypatch.delenv("AWOS_LOOP_BREAKER", raising=False)
    sentinel = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2))
    seen = {}

    class Inner:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**kw):
                    seen.update(kw)
                    return sentinel
    tap = _UsageTap(Inner)
    out = tap.create(model="m", messages=[], max_tokens=5)
    assert out is sentinel
    assert seen == {"model": "m", "messages": [], "max_tokens": 5}


def test_one_shot_tap_on_streams_and_logs(monkeypatch, tmp_path):
    from one_shot import _UsageTap
    from providers import utility_chat
    monkeypatch.setenv("AWOS_LOOP_BREAKER", "1")
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("AWOS_LLM_CALL_LOG", str(log))
    fake = FakeClient([LOOPING, CLEAN])
    inner = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    tap = _UsageTap(inner)
    text, info = utility_chat(tap, "one_shot", "deepseek/deepseek-v4-flash",
                              [{"role": "user", "content": "x"}], max_tokens=100,
                              send_reasoning=False, reasoning=None, retry=False)
    assert text == CLEAN
    assert tap.output_tokens > 50
    import json
    line = json.loads(log.read_text().splitlines()[-1])
    assert line["loop_guard"] == "resampled"


def test_agent_loop_client_uses_guard_when_on(monkeypatch):
    from agent_loop import OpenAIToolClient
    monkeypatch.setenv("AWOS_LOOP_BREAKER", "1")
    fake = FakeClient([LOOPING, "All done."])
    client = OpenAIToolClient(SimpleNamespace(chat=SimpleNamespace(completions=fake)), "m")
    reg = SimpleNamespace(openai_schemas=lambda: [])
    reply = client.complete("sys", [{"role": "user", "content": "go"}], reg)
    assert reply.text == "All done."
    assert reply.raw.loop_guard == "resampled"


def test_agent_loop_client_default_off_unchanged(monkeypatch):
    from agent_loop import OpenAIToolClient
    monkeypatch.delenv("AWOS_LOOP_BREAKER", raising=False)
    fake = FakeClient([LOOPING])
    client = OpenAIToolClient(SimpleNamespace(chat=SimpleNamespace(completions=fake)), "m")
    reply = client.complete("sys", [], SimpleNamespace(openai_schemas=lambda: []))
    assert "stream" not in fake.calls[0]
    assert reply.text == LOOPING


# ── false positives on real replies (skipped when no corpus is present) ────

def test_false_positive_rate_on_solved_runs():
    sys.path.insert(0, str(ROOT / "scripts"))
    import loop_guard_corpus
    stats = loop_guard_corpus.measure()
    if stats["solved_runs"] < 10:
        pytest.skip("no local corpus of solved runs")
    assert stats["solved_trip_rate"] <= 0.02, stats
