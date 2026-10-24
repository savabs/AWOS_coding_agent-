"""
loop_grammar.py — grammar-constrained loop replies for the local model
(AWOS_LOOP_GRAMMAR; docs/research/rea_synthesis_2026-10-16.md item B1).

Why: on llama-server the loop executor's tool calls go through the chat
template's tool parsing (--jinja). A 9B model emits malformed or missing tool
calls often enough to burn steps on format-error nudges. Here the reply is
plain text whose shape a GBNF grammar fixes at sampling time, and AWOS parses
it itself:

    <0..PRE_MAX_LINES lines of free reasoning, each <= PRE_LINE_MAX chars>
    EITHER
    ```bash
    <the command, one or more lines>
    ```
    OR
    COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT
    <0..SUMMARY_MAX_LINES summary lines>

Exactly one action per reply: after the closing fence the grammar admits
nothing more, so the server stops at once.

Envelope: a fenced ```bash block, not a JSON object {"command": "..."}.
A JSON envelope makes the model write the command as a JSON string: every
quote, backslash and newline escaped. Loop commands are heredocs, sed
regexes and python -c one-liners, so that escaping lands exactly where a
small model is weakest. The grammar would keep the JSON valid but not the
command right (`\\n` vs `\\\\n` both parse), and Qwen's tokenizer has no
merged tokens for escaped code, so it costs more tokens per command. A
```bash fence is the format these models write in pretraining and
mini-swe-agent's text mode uses; the command bytes are the shell bytes, with
nothing to unescape. The one restriction: no command line may start with
three backticks (it would close the fence), and the first command line must
not start with a backtick.

Wire: llama-server's OpenAI-compatible /v1/chat/completions takes `grammar`
(GBNF text) as a top-level body field (sent through the SDK's extra_body).
It refuses a custom grammar together with `tools` (the tool-call grammar is
its own), so in this mode no `tools` are sent: the action is parsed here,
the assistant turn goes back as its raw text and the observation as a plain
user message. `chat_template_kwargs.enable_thinking=false` keeps a thinking
model (Qwen3.x) out of a <think> block the grammar does not admit; the
preamble is where it reasons.

Prefix drift (telemetry): llama-server reports per call `timings.cache_n`
(prompt tokens reused from the slot) and `prompt_n` / `predicted_n`. When a
request's messages extend the previous request's messages plus the previous
reply, everything the slot held after the previous call (prompt_n + cache_n +
predicted_n, minus DRIFT_SLACK_TOKENS for the stop token, which is sampled but
never decoded into the KV) should be reused. A shortfall means the server
re-rendered earlier history differently from what it generated against:
the client counts it in `prefix_drift_calls` / `prefix_drift_tokens` and puts
the call's shortfall on `reply.prefix_drift` (int tokens; None when the call
cannot be judged: first call, history rewritten/compacted, no timings).

Qwen3 chat-template concern (AWOS_LOOP_THINK_STUB). With
enable_thinking=false the generation prompt ends in an empty think block,
`<|im_start|>assistant\n<think>\n\n</think>\n\n`, so the slot's KV holds that
stub before each reply. When the same turn comes back as history, Qwen3's
template renders the think block only for assistant turns after the *last
real user query* and only when reasoning_content is non-empty (or the turn
is the last message); a stub written into `content` is split off by the
template itself. Observations sent as plain user messages move the "last
query" forward every step, so every earlier assistant turn is re-rendered
without the stub: the cached prefix breaks at the previous reply on every
step (about one reply + one observation re-prefilled per call).
AWOS_LOOP_THINK_STUB=1 sends observations with the `tool` role (Qwen3 renders
them as `<tool_response>` inside a user turn, which does not count as a
query) and gives each assistant turn reasoning_content="\n" (non-empty, so
the block is rendered; stripped of newlines, so it is exactly the empty
stub). Default off until measured on the real template: the Qwen3.5 GGUF's
template has not been checked here, and prefix_drift tells the two apart.

Default: on when AWOS_PROVIDER=local (providers.local_mode()) and the client
is the OpenAI-compatible one; AWOS_LOOP_GRAMMAR=0 turns it off, =1 forces it
for any OpenAI-compatible client whose server takes `grammar`. The Anthropic
client is never wrapped, whatever the env says.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

try:
    from .agent_loop import (ModelReply, OpenAIToolClient, ToolCall, _render_result,
                             _usage_counts, response_timings)
    from .edit_grammar import literal
except ImportError:  # flat sys.path import
    from agent_loop import (ModelReply, OpenAIToolClient, ToolCall, _render_result,
                            _usage_counts, response_timings)
    from edit_grammar import literal

ENV_VAR = "AWOS_LOOP_GRAMMAR"
THINK_STUB_ENV = "AWOS_LOOP_THINK_STUB"
#: tokens of the previous call that may legitimately be missing from the next
#: call's cache: the stop token is sampled (counted in predicted_n) but not decoded.
DRIFT_SLACK_TOKENS = 1
SUBMIT_MARKER = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
FENCE_OPEN = "```bash"
FENCE = "```"

PRE_MAX_LINES = 24
PRE_LINE_MAX = 400
SUMMARY_MAX_LINES = 16
SUMMARY_LINE_MAX = 400


def _not_marker_rest(marker: str) -> str:
    """GBNF alternatives for a line starting with marker[0] that is NOT the
    marker; the remainder of the line is the bounded rule `prest`."""
    alts = []
    for k in range(1, len(marker)):
        head = literal(marker[:k])
        c = marker[k]
        alts.append(f"{head} [^{c}\\n] prest")
        alts.append(head)
    return " | ".join(f"( {a} )" for a in alts)


def build_grammar(marker: str = SUBMIT_MARKER) -> str:
    """The loop-reply GBNF (llama.cpp dialect: {m,n} repetition, [^...] classes)."""
    first = marker[0]
    rules = [
        "root ::= pre ( act | sub )",
        # Reasoning preamble: bounded lines, none starting with "`" or the marker,
        # so the action/submit envelope always starts on its own line.
        f"pre ::= pline{{0,{PRE_MAX_LINES}}}",
        f"pline ::= ( [^`{first}\\n] prest | {_not_marker_rest(marker)} )? \"\\n\"",
        f"prest ::= [^\\n]{{0,{PRE_LINE_MAX - 1}}}",
        # One bash action in a fixed fence; nothing after the closing fence.
        f"act ::= {literal(FENCE_OPEN + chr(10))} cfirst cline* {literal(FENCE)}",
        "cfirst ::= [ \\t]* [^`\\n \\t] [^\\n]* \"\\n\"",
        "cline ::= ( [^`\\n] [^\\n]* | \"`\" ( [^`\\n] [^\\n]* )? "
        "| \"``\" ( [^`\\n] [^\\n]* )? )? \"\\n\"",
        # Or the submit line and a short summary.
        f"sub ::= {literal(marker)} ( \"\\n\" sline ){{0,{SUMMARY_MAX_LINES}}}",
        f"sline ::= [^\\n]{{0,{SUMMARY_LINE_MAX}}}",
    ]
    return "\n".join(rules) + "\n"


GRAMMAR = build_grammar()


# ── parsing ──────────────────────────────────────────────────────────────────


@dataclass
class ParsedReply:
    """kind: bash | submit | invalid."""

    kind: str
    command: str = ""
    summary: str = ""
    preamble: str = ""
    error: str = ""


def parse_reply(text: str) -> ParsedReply:
    """Parse a loop reply in the fenced envelope.

    Under the grammar every reply is valid by construction, and the strict
    pass (envelope lines at column 0, as the grammar has them) reads it
    exactly as the grammar does. For an unconstrained reply a lenient pass
    (indented fences / marker, CRLF, text after the block) follows; the
    result says why when neither can parse it."""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    strict = _parse_lines(lines, lambda s: s)
    if strict.kind != "invalid":
        return strict
    lenient = _parse_lines(lines, lambda s: s.strip())
    return lenient if lenient.kind != "invalid" else strict


def _parse_lines(lines: list, norm) -> ParsedReply:
    for i, line in enumerate(lines):
        bare = norm(line)
        if bare.startswith(SUBMIT_MARKER):
            rest = bare[len(SUBMIT_MARKER):].strip()
            summary = "\n".join(([rest] if rest else []) + lines[i + 1:]).strip()
            return ParsedReply("submit", summary=summary, preamble="\n".join(lines[:i]).strip())
        if bare.rstrip() == FENCE_OPEN:
            body = []
            for line2 in lines[i + 1:]:
                if norm(line2).startswith(FENCE):
                    command = "\n".join(body)
                    if not command.strip():
                        return ParsedReply("invalid", error="empty bash block")
                    return ParsedReply("bash", command=command,
                                       preamble="\n".join(lines[:i]).strip())
                body.append(line2)
            return ParsedReply("invalid", error="unclosed ```bash block")
    return ParsedReply("invalid", error="no ```bash block and no submit line")


# ── enablement ───────────────────────────────────────────────────────────────


def _env() -> str:
    return os.getenv(ENV_VAR, "").strip().lower()


def _local_mode() -> bool:
    try:
        from .providers import local_mode
    except ImportError:
        from providers import local_mode
    return local_mode()


def think_stub_enabled() -> bool:
    return os.getenv(THINK_STUB_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def _slot_pin() -> Optional[int]:
    """AWOS_LOCAL_SLOT_ID (default 0) when AWOS_LOCAL_SLOTS > 1, else None."""
    try:
        if int(os.getenv("AWOS_LOCAL_SLOTS", "1").strip() or 1) <= 1:
            return None
        return max(0, int(os.getenv("AWOS_LOCAL_SLOT_ID", "0").strip() or 0))
    except ValueError:
        return None


def _tint(timings: Any, key: str) -> Optional[int]:
    try:
        value = timings.get(key)
        return None if value is None or isinstance(value, bool) else max(0, int(value))
    except (AttributeError, TypeError, ValueError):
        return None


def prefix_drift(prev_timings: Any, timings: Any, slack: int = DRIFT_SLACK_TOKENS
                 ) -> Optional[int]:
    """Tokens the previous call left in the slot that this call did not reuse.

    expected = prev.prompt_n + prev.cache_n + prev.predicted_n - slack; drift =
    max(0, expected - cache_n). None when either call lacks the counts. Only
    meaningful when this request extends the previous one (caller checks)."""
    if not isinstance(prev_timings, dict) or not isinstance(timings, dict):
        return None
    parts = [_tint(prev_timings, k) for k in ("prompt_n", "cache_n", "predicted_n")]
    cache_n = _tint(timings, "cache_n")
    if cache_n is None or parts[0] is None or parts[2] is None:
        return None
    expected = parts[0] + (parts[1] or 0) + parts[2] - max(0, slack)
    return max(0, expected - cache_n)


def enabled_for(client: Any) -> bool:
    """True when `client`'s loop replies should be grammar-constrained."""
    if isinstance(client, GrammarLoopClient):
        return False  # already wrapped
    if not isinstance(client, OpenAIToolClient):
        return False  # Anthropic (and scripted test clients) never
    env = _env()
    if env in ("0", "false", "no", "off"):
        return False
    if env in ("1", "true", "yes", "on"):
        return True
    return _local_mode()


def wrap_for_loop(client: Any) -> Any:
    """GrammarLoopClient(client) when enabled_for(client), else `client` unchanged."""
    return GrammarLoopClient(client) if enabled_for(client) else client


# ── the client ───────────────────────────────────────────────────────────────


class GrammarLoopClient:
    """Text-protocol loop client over an OpenAIToolClient's transport.

    Same interface as the tool clients (complete / format_assistant_turn /
    format_tool_results), so LoopExecutor drives it unchanged; a parsed bash
    action comes back as one ToolCall named "bash". Request body: model,
    max_tokens, [system, *messages], extra_body {grammar, chat_template_kwargs}
    and nothing else that varies per step (no tools, no ids, no timestamps)."""

    text_protocol = True

    def __init__(self, inner: OpenAIToolClient, grammar: str = GRAMMAR) -> None:
        self.inner = inner
        self._client = inner._client
        self.model = inner.model
        self.max_tokens = inner.max_tokens
        self.grammar = grammar
        self.parse_failures = 0
        self._calls = 0
        #: calls whose cache_n fell short of what the previous call left in the slot
        self.prefix_drift_calls = 0
        self.prefix_drift_tokens = 0
        #: calls that could be judged (request extended the previous one, timings present)
        self.prefix_drift_checked = 0
        self.think_stub = think_stub_enabled()
        # With more than one server slot, pin the loop to the slot the static prefix
        # was restored into (local_runtime.prepare_first_step, AWOS_LOCAL_SLOT_ID);
        # with one slot nothing is sent, so the request bytes are unchanged.
        self.id_slot = _slot_pin()
        self._prev: Optional[tuple] = None   # (messages sent, reply text, timings)

    def request_kwargs(self, system: str, messages: list) -> dict:
        extra = {"grammar": self.grammar, "chat_template_kwargs": {"enable_thinking": False}}
        if self.id_slot is not None:
            extra["id_slot"] = self.id_slot
        return dict(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{"role": "system", "content": system}, *messages],
            extra_body=extra,
        )

    def complete(self, system, messages, registry=None, tool_choice: Optional[str] = None
                 ) -> ModelReply:
        kwargs = self.request_kwargs(system, messages)
        try:
            from . import loop_guard
        except ImportError:
            import loop_guard
        if loop_guard.enabled():
            response = loop_guard.guarded_create(
                self._client.chat.completions.create, kwargs, component="loop")
        else:
            response = self._client.chat.completions.create(**kwargs)
        self._calls += 1
        message = response.choices[0].message
        text = getattr(message, "content", "") or ""
        timings = response_timings(response)
        drift = self._drift(kwargs["messages"], timings)
        # Shallow copies: the loop may edit earlier turns in place (compaction).
        self._prev = ([dict(m) for m in kwargs["messages"]], text, timings)
        parsed = parse_reply(text)
        calls = []
        if parsed.kind == "bash":
            # Never rendered into the prompt (text protocol), so a counter is enough.
            calls.append(ToolCall(id=f"g{self._calls}", name="bash",
                                  arguments={"command": parsed.command}))
        elif parsed.kind == "invalid":
            self.parse_failures += 1
        usage = getattr(response, "usage", None)
        counts = _usage_counts(usage, response_timings(response))
        reply = ModelReply(
            text=text,
            tool_calls=calls,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            raw=response,
            cached_tokens=counts.get("cached", 0),
            cache_write_tokens=counts.get("cache_write", 0),
        )
        # Telemetry for the loop to sum: tokens of drift on this call (None =
        # not judged) and llama-server's speculative-draft counts when present.
        reply.prefix_drift = drift
        reply.draft_tokens = counts.get("draft", 0)
        reply.draft_accepted = counts.get("draft_accepted", 0)
        return reply

    def _drift(self, messages: list, timings: Any) -> Optional[int]:
        if self._prev is None:
            return None
        prev_messages, prev_text, prev_timings = self._prev
        n = len(prev_messages)
        # Judge only a request that extends the previous one with the previous
        # reply as the next turn; a rewritten/compacted history is a reset.
        if len(messages) <= n or messages[:n] != prev_messages:
            return None
        nxt = messages[n]
        if nxt.get("role") != "assistant" or (nxt.get("content") or "") != prev_text:
            return None
        drift = prefix_drift(prev_timings, timings)
        if drift is None:
            return None
        self.prefix_drift_checked += 1
        if drift > 0:
            self.prefix_drift_calls += 1
            self.prefix_drift_tokens += drift
        return drift

    def format_assistant_turn(self, reply: ModelReply) -> dict[str, Any]:
        # The raw text, byte for byte: what the model generated is what the
        # next prompt re-renders, so the server's KV prefix stays valid.
        turn = {"role": "assistant", "content": reply.text or ""}
        if self.think_stub:
            # Non-empty so Qwen3's template renders the block; "\n".strip("\n")
            # is empty, so it renders exactly the generation prompt's empty stub.
            turn["reasoning_content"] = "\n"
        return turn

    def format_tool_results(self, calls, results) -> list[dict[str, Any]]:
        if self.think_stub:
            ids = [getattr(c, "id", "") for c in (calls or [])]
            return [{"role": "tool", "tool_call_id": ids[i] if i < len(ids) else f"g{i}",
                     "content": _render_result(result)} for i, result in enumerate(results)]
        return [{"role": "user", "content": _render_result(result)} for result in results]


def grammar_digest(grammar: str = GRAMMAR) -> str:
    """Short stable id of the grammar text (for logs / slot keys)."""
    import hashlib
    return hashlib.sha256(grammar.encode("utf-8")).hexdigest()[:12]


__all__ = ["GRAMMAR", "GrammarLoopClient", "ParsedReply", "build_grammar", "enabled_for",
           "grammar_digest", "parse_reply", "prefix_drift", "think_stub_enabled",
           "wrap_for_loop"]
