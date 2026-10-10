"""
loop_guard.py — detect a model repeating itself and cut the reply short.

Small and cheap models (DeepSeek Flash, 9B local models) sometimes fall into
a verbatim loop and keep writing until the output cap — 16k tokens of nothing
usable. This module detects the loop while the reply streams, aborts it,
trims the text back to before the repetition began, and asks once more.

Detect, don't penalise: a sampling penalty (DRY, repetition penalty) also
hits the verbatim copying a SEARCH block needs, so we leave sampling alone
and only watch the text. Text inside SEARCH/REPLACE edit blocks is ignored
(verbatim copies of a file are expected there). Spec: docs/specs/loop_breaker.md.

Signals (outside edit blocks):
  - self_match:     the tail of the text (>= SELF_MATCH_CHARS, ~64 tokens)
                    already occurred earlier in the text.
  - repeated_lines: >= REPEAT_LINES consecutive identical non-blank lines.
  - repeated_block: >= REPEAT_BLOCKS consecutive identical edit blocks.
Inside an edit block only a very long run of identical lines
(>= IN_BLOCK_REPEAT_LINES) counts, which a real file copy does not produce.

Env:
    AWOS_LOOP_BREAKER=1   on (default "0": off, byte-identical behaviour)
    AWOS_LOOP_BREAKER_RESAMPLE_TEMP  temperature for the one resample when
                          the first call ran at 0 (default 0.3; a temperature-0
                          resample tends to repeat the same loop)
"""
from __future__ import annotations

import logging
import os
import re
import sys
from types import SimpleNamespace
from typing import Any, Optional

logger = logging.getLogger(__name__)

ENV = "AWOS_LOOP_BREAKER"
SELF_MATCH_CHARS = 256          # ~64 tokens at ~4 chars/token
REPEAT_LINES = 4
REPEAT_BLOCKS = 3
IN_BLOCK_REPEAT_LINES = 50
MIN_LINE_CHARS = 3              # "}" / ")" runs are normal code, not loops
LONG_PARTIAL_CHARS = 2048       # check a newline-free run this long too
CHECK_EVERY = 64                # chars between self-match checks
# A self-match counts only as a tandem repeat (the repeated text directly
# follows its earlier copy, "R R"); see docs/specs/loop_breaker.md §3.
REQUIRE_TANDEM = True

# Same markers as one_shot.parse_blocks.
_HEAD = re.compile(r"^\s*<{5,9} ?SEARCH\b.*$")
_DIV = re.compile(r"^\s*={5,9}\s*$")
_TAIL = re.compile(r"^\s*>{5,9} ?REPLACE\b.*$")


def enabled() -> bool:
    return os.environ.get(ENV, "0").strip().lower() in ("1", "true", "yes", "on")


def resample_temperature(original: Optional[float]) -> Optional[float]:
    if original:
        return original
    try:
        return float(os.environ.get("AWOS_LOOP_BREAKER_RESAMPLE_TEMP", "0.3"))
    except ValueError:
        return 0.3


class Trip:
    """Where and why the detector fired: `cut` is the offset in the full
    text to keep up to (the repetition begins there)."""

    def __init__(self, kind: str, at: int, cut: int) -> None:
        self.kind, self.at, self.cut = kind, at, max(0, cut)

    def __repr__(self) -> str:  # pragma: no cover
        return f"Trip({self.kind!r}, at={self.at}, cut={self.cut})"


class LoopDetector:
    """Incremental: feed() text chunks as they stream; returns a Trip once."""

    def __init__(self) -> None:
        self.text = ""              # everything fed so far
        self._line_start = 0        # offset of the current (partial) line
        self._in_block = False
        self._block_lines: list[str] = []
        self._blocks: list[str] = []     # completed blocks, most recent last
        # Outside-block buffer: contiguous text since the last completed block.
        self._buf = ""
        self._buf_origin = 0
        self._last_check = 0
        # Run of identical lines: (line, count, offset of the 2nd occurrence)
        self._run_line: Optional[str] = None
        self._run_count = 0
        self._run_cut = 0
        self._blk_run_line: Optional[str] = None
        self._blk_run_count = 0
        self._blk_run_cut = 0
        self.trip: Optional[Trip] = None

    # ── feeding ──
    def feed(self, chunk: str) -> Optional[Trip]:
        if self.trip is not None or not chunk:
            return self.trip
        self.text += chunk
        while True:
            nl = self.text.find("\n", self._line_start)
            if nl < 0:
                break
            line = self.text[self._line_start:nl]
            start = self._line_start
            self._line_start = nl + 1
            trip = self._line(line, start)
            if trip:
                self.trip = trip
                return trip
        partial = len(self.text) - self._line_start
        if not self._in_block and partial >= LONG_PARTIAL_CHARS:
            trip = self._self_match(self._buf + self.text[self._line_start:], force=True)
            if trip:
                self.trip = trip
        return self.trip

    def finish(self) -> Optional[Trip]:
        """Process a trailing line with no newline (post-hoc use)."""
        if self.trip is None and self._line_start < len(self.text):
            self.feed("\n")
        return self.trip

    # ── per line ──
    def _line(self, line: str, start: int) -> Optional[Trip]:
        if self._in_block:
            if _TAIL.match(line):
                self._close_block()
                return self._repeated_block(start)
            if _HEAD.match(line):          # malformed: new head before tail
                self._block_lines = []
                return None
            self._block_lines.append(line)
            return self._in_block_run(line, start)
        if _HEAD.match(line):
            self._in_block = True
            self._block_lines = [line]
            self._blk_run_line, self._blk_run_count = None, 0
            self._run_line, self._run_count = None, 0
            return None
        self._buf += line + "\n"
        trip = self._repeated_line(line, start)
        if trip:
            return trip
        # Without the final newline: a runaway's last copy is usually cut
        # mid-way, so the tail need not end where an earlier copy did.
        return self._self_match(self._buf[:-1])

    def _close_block(self) -> None:
        self._blocks.append("\n".join(self._block_lines))
        self._in_block = False
        self._block_lines = []
        # A finished block is progress: the outside-text history restarts, so
        # the short "path / ``` " preambles of many blocks never add up to a
        # false self-match.
        self._buf = ""
        self._buf_origin = self._line_start
        self._last_check = 0

    def _repeated_block(self, start: int) -> Optional[Trip]:
        if len(self._blocks) < REPEAT_BLOCKS:
            return None
        last = self._blocks[-REPEAT_BLOCKS:]
        if all(b == last[0] for b in last) and last[0].strip():
            # Keep the first copy: cut at the start of the 2nd copy's head.
            cut = _nth_from_end(self.text, last[0], REPEAT_BLOCKS - 1, before=self._line_start)
            return Trip("repeated_block", self._line_start, cut if cut is not None else start)
        return None

    def _repeated_line(self, line: str, start: int) -> Optional[Trip]:
        key = line.rstrip()
        if not key.strip():
            return None                     # blank lines neither count nor break
        if key == self._run_line:
            self._run_count += 1
            if self._run_count == 2:
                self._run_cut = start
        else:
            self._run_line, self._run_count = key, 1
        if self._run_count >= REPEAT_LINES and len(key.strip()) >= MIN_LINE_CHARS:
            return Trip("repeated_lines", self._line_start, self._run_cut)
        return None

    def _in_block_run(self, line: str, start: int) -> Optional[Trip]:
        key = line.rstrip()
        if not key.strip():
            return None
        if key == self._blk_run_line:
            self._blk_run_count += 1
            if self._blk_run_count == 2:
                self._blk_run_cut = start
        else:
            self._blk_run_line, self._blk_run_count = key, 1
        if self._blk_run_count >= IN_BLOCK_REPEAT_LINES and len(key.strip()) >= MIN_LINE_CHARS:
            return Trip("repeated_lines_in_block", self._line_start, self._blk_run_cut)
        return None

    def _self_match(self, buf: str, force: bool = False) -> Optional[Trip]:
        n = len(buf)
        if n < SELF_MATCH_CHARS + 1:
            return None
        if not force and n - self._last_check < CHECK_EVERY:
            return None
        self._last_check = n
        tail = buf[n - SELF_MATCH_CHARS:]
        p = buf.rfind(tail, 0, n - 1)       # an earlier occurrence (may overlap)
        if p < 0:
            return None
        period = (n - SELF_MATCH_CHARS) - p
        # Extend the match backwards: buf[i] == buf[i - period] from the end.
        i = n - 1
        while i - period >= 0 and buf[i] == buf[i - period]:
            i -= 1
        # buf[i+1:] repeats buf[i+1-period:n-period]; keep one copy.
        cut_in_buf = i + 1
        if REQUIRE_TANDEM and (n - cut_in_buf) < period:
            # The earlier copy is not adjacent: the model quoted the same text
            # twice with other text between (e.g. re-quoting a function before
            # its SEARCH block). That is not a loop.
            return None
        return Trip("self_match", self._buf_origin + n, self._buf_origin + cut_in_buf)


def _nth_from_end(text: str, needle: str, n: int, before: int) -> Optional[int]:
    """Offset of the n-th occurrence of `needle` counting back from `before`
    (n=1 is the last one)."""
    end = before
    pos = None
    for _ in range(n):
        pos = text.rfind(needle, 0, end)
        if pos < 0:
            return None
        end = pos
    return pos


def detect(text: str) -> Optional[Trip]:
    """Post-hoc: run the detector over a whole reply."""
    d = LoopDetector()
    d.feed(text or "")
    return d.finish()


def truncate(text: str, trip: Trip) -> str:
    return (text or "")[: trip.cut].rstrip() + ("\n" if trip.cut else "")


# ── Guarded call ─────────────────────────────────────────────────────────────

def _say(msg: str) -> None:
    logger.info(msg)
    try:
        print(msg, file=sys.stderr, flush=True)
    except Exception:
        pass


def _estimate_tokens(chars: int) -> int:
    return max(0, (chars + 3) // 4)


def _prompt_chars(messages: Any) -> int:
    total = 0
    for m in messages or []:
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, str):
            total += len(c)
        elif isinstance(c, list):
            total += sum(len(str(p.get("text", ""))) for p in c if isinstance(p, dict))
    return total


def _g(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    v = getattr(obj, name, None)
    if v is None:
        extra = getattr(obj, "model_extra", None)
        if isinstance(extra, dict):
            v = extra.get(name)
    return v


class _Attempt:
    def __init__(self) -> None:
        self.content = ""
        self.reasoning = ""
        self.tool_calls: dict[int, dict] = {}
        self.finish: Optional[str] = None
        self.usage: Any = None
        self.model: Optional[str] = None
        self.provider: Optional[str] = None
        self.id: Optional[str] = None
        self.trip: Optional[Trip] = None
        self.trip_stream = "content"
        self.streamed = True


def _stream_once(create: Any, kwargs: dict) -> _Attempt:
    """One streamed call watched by two detectors (content, reasoning)."""
    att = _Attempt()
    body = dict(kwargs)
    body["stream"] = True
    body["stream_options"] = {"include_usage": True}
    stream = create(**body)
    content_det, reason_det = LoopDetector(), LoopDetector()
    try:
        for chunk in stream:
            att.model = att.model or _g(chunk, "model")
            att.id = att.id or _g(chunk, "id")
            att.provider = att.provider or _g(chunk, "provider")
            usage = _g(chunk, "usage")
            if usage is not None:
                att.usage = usage
            for choice in _g(chunk, "choices") or []:
                delta = _g(choice, "delta")
                fr = _g(choice, "finish_reason")
                if fr:
                    att.finish = fr
                piece = _g(delta, "content")
                if isinstance(piece, str) and piece:
                    att.content += piece
                    t = content_det.feed(piece)
                    if t:
                        att.trip, att.trip_stream = t, "content"
                rpiece = _g(delta, "reasoning") or _g(delta, "reasoning_content")
                if isinstance(rpiece, str) and rpiece:
                    att.reasoning += rpiece
                    t = reason_det.feed(rpiece)
                    if t and att.trip is None:
                        att.trip, att.trip_stream = t, "reasoning"
                for tc in _g(delta, "tool_calls") or []:
                    idx = _g(tc, "index") or 0
                    slot = att.tool_calls.setdefault(idx, {"id": None, "name": "", "arguments": ""})
                    if _g(tc, "id"):
                        slot["id"] = _g(tc, "id")
                    fn = _g(tc, "function")
                    if _g(fn, "name"):
                        slot["name"] += _g(fn, "name")
                    if _g(fn, "arguments"):
                        slot["arguments"] += _g(fn, "arguments")
                if att.trip is not None:
                    break
            if att.trip is not None:
                break
    finally:
        if att.trip is not None:
            close = getattr(stream, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
    if att.trip is None:
        t = content_det.finish()
        if t:
            att.trip, att.trip_stream = t, "content"
    return att


def _plain_once(create: Any, kwargs: dict) -> tuple[_Attempt, Any]:
    """Non-streamed call + post-hoc detection (backend cannot stream)."""
    response = create(**kwargs)
    att = _Attempt()
    att.streamed = False
    att.usage = _g(response, "usage")
    att.model = _g(response, "model")
    att.provider = _g(response, "provider")
    try:
        choice = response.choices[0]
        att.content = choice.message.content or ""
        att.finish = getattr(choice, "finish_reason", None)
        for i, tc in enumerate(getattr(choice.message, "tool_calls", None) or []):
            att.tool_calls[i] = {"id": tc.id, "name": tc.function.name,
                                 "arguments": tc.function.arguments}
    except (AttributeError, IndexError, TypeError):
        pass
    att.trip = detect(att.content)
    return att, response


def _tokens(att: _Attempt, prompt_chars: int) -> tuple[int, int, bool]:
    """(prompt, completion, estimated) for one attempt."""
    pt = _g(att.usage, "prompt_tokens")
    ct = _g(att.usage, "completion_tokens")
    if isinstance(pt, int) and isinstance(ct, int):
        return pt, ct, False
    return (_estimate_tokens(prompt_chars),
            _estimate_tokens(len(att.content) + len(att.reasoning)
                             + sum(len(s["arguments"]) for s in att.tool_calls.values())),
            True)


def _build_response(att: _Attempt, text: str, finish: Optional[str], attempts: list,
                    prompt_chars: int, state: str) -> Any:
    pt = ct = 0
    estimated = False
    costs = []
    for a in attempts:
        p, c, est = _tokens(a, prompt_chars)
        pt, ct, estimated = pt + p, ct + c, estimated or est
        cost = _g(a.usage, "cost")
        costs.append(cost if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None)
    usage = SimpleNamespace(
        prompt_tokens=pt, completion_tokens=ct, total_tokens=pt + ct,
        cost=(sum(costs) if costs and all(c is not None for c in costs) else None),
        # One clean attempt: keep the provider's cache / reasoning details.
        prompt_tokens_details=(_g(att.usage, "prompt_tokens_details") if len(attempts) == 1 else None),
        completion_tokens_details=(_g(att.usage, "completion_tokens_details")
                                   if len(attempts) == 1 else None))
    calls = [SimpleNamespace(id=s["id"] or f"call_{i}", type="function",
                             function=SimpleNamespace(name=s["name"], arguments=s["arguments"]))
             for i, s in sorted(att.tool_calls.items())]
    message = SimpleNamespace(role="assistant", content=text, tool_calls=calls or None)
    return SimpleNamespace(
        id=att.id, model=att.model, provider=att.provider,
        choices=[SimpleNamespace(index=0, message=message, finish_reason=finish)],
        usage=usage, loop_guard=state, loop_guard_tokens_estimated=estimated or None)


def guarded_create(create: Any, kwargs: dict, *, component: str = "llm") -> Any:
    """
    `create(**kwargs)` with the loop breaker: stream, abort on a loop, trim,
    resample once. Returns an OpenAI-shaped response. When nothing tripped
    and the backend streamed, the response is rebuilt from the stream;
    `response.loop_guard` is None (clean), "resampled" (first tripped, retry
    clean) or "tripped" (both tripped: the trimmed text is kept and
    finish_reason is "length", so callers take their cut-off path).
    Errors from the backend propagate unchanged.
    """
    prompt_chars = _prompt_chars(kwargs.get("messages"))
    use_stream = not kwargs.get("stream")
    attempts: list[_Attempt] = []

    def once(kw: dict) -> tuple[_Attempt, Any]:
        nonlocal use_stream
        if use_stream:
            try:
                return _stream_once(create, kw), None
            except TypeError:
                use_stream = False      # client does not take stream kwargs
        return _plain_once(create, kw)

    first, raw = once(kwargs)
    attempts.append(first)
    if first.trip is None:
        if raw is not None:
            return raw                  # non-streamed, clean: the real response
        return _build_response(first, first.content, first.finish, attempts,
                               prompt_chars, None)
    kept = truncate(first.content, first.trip) if first.trip_stream == "content" else first.content
    _say(f"[LOOP-GUARD] tripped: {first.trip.kind} ({first.trip_stream}) after "
         f"{first.trip.at} chars; resampled [{component}]")
    retry_kwargs = dict(kwargs)
    temp = resample_temperature(kwargs.get("temperature"))
    if temp is not None:
        retry_kwargs["temperature"] = temp
    second, _ = once(retry_kwargs)
    attempts.append(second)
    if second.trip is None:
        return _build_response(second, second.content, second.finish, attempts,
                               prompt_chars, "resampled")
    kept2 = (truncate(second.content, second.trip)
             if second.trip_stream == "content" else second.content)
    _say(f"[LOOP-GUARD] tripped again: {second.trip.kind} ({second.trip_stream}) after "
         f"{second.trip.at} chars; keeping the trimmed reply [{component}]")
    best, text = (second, kept2) if len(kept2) >= len(kept) else (first, kept)
    return _build_response(best, text, "length", attempts, prompt_chars, "tripped")
