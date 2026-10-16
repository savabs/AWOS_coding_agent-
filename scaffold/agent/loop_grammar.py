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

    def request_kwargs(self, system: str, messages: list) -> dict:
        return dict(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{"role": "system", "content": system}, *messages],
            extra_body={"grammar": self.grammar,
                        "chat_template_kwargs": {"enable_thinking": False}},
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
        return ModelReply(
            text=text,
            tool_calls=calls,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            raw=response,
            cached_tokens=counts.get("cached", 0),
            cache_write_tokens=counts.get("cache_write", 0),
        )

    def format_assistant_turn(self, reply: ModelReply) -> dict[str, Any]:
        # The raw text, byte for byte: what the model generated is what the
        # next prompt re-renders, so the server's KV prefix stays valid.
        return {"role": "assistant", "content": reply.text or ""}

    def format_tool_results(self, calls, results) -> list[dict[str, Any]]:
        return [{"role": "user", "content": _render_result(result)} for result in results]


def grammar_digest(grammar: str = GRAMMAR) -> str:
    """Short stable id of the grammar text (for logs / slot keys)."""
    import hashlib
    return hashlib.sha256(grammar.encode("utf-8")).hexdigest()[:12]


__all__ = ["GRAMMAR", "GrammarLoopClient", "ParsedReply", "build_grammar", "enabled_for",
           "grammar_digest", "parse_reply", "wrap_for_loop"]
