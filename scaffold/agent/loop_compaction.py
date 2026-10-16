"""
loop_compaction.py — two-stage context compaction for the loop executor
(rea_synthesis_2026-10-16 item 10; Cursor / Devin / Amp pattern).

Stage 1 (maybe_start): once the prompt reaches `start_frac` (~70 %) of the
model's window, the older part of the conversation is summarised in a
background thread. Stage 2 (maybe_swap): at `swap_frac` (~85 %) the summary
replaces that part. The goal message stays verbatim, the last `keep_recent`
turns stay verbatim, and the cut never separates a tool call from its result.

Default OFF (AWOS_LOOP_COMPACT=1 turns it on): a swap throws away the prompt
cache of everything after the goal, so it trades cache hits for room and needs
its own pre-registered A/B (primary: solve rate on tasks over 60 steps, with a
cost check).

Message shapes handled (whatever the loop's client produces):
  * Anthropic: assistant content = [text, tool_use{id}...]; results = one user
    message whose content is [tool_result{tool_use_id}...];
  * OpenAI: assistant {content, tool_calls[{id}]}; results = role "tool"
    messages with tool_call_id;
  * text protocol (loop_grammar): plain {"role", "content": str} messages.

Shape of the compacted list (merge_summary=True, the default):
    [goal message + summary block, assistant, ...tail verbatim...]
The summary is appended to the goal message's content (the goal's bytes stay a
verbatim prefix), so roles strictly alternate user/assistant: valid for
Anthropic, OpenAI and the strict chat templates some local models use (which
reject two user messages in a row). merge_summary=False instead inserts the
summary as its own user message after the goal: [goal, summary, assistant,
...]; Anthropic and OpenAI accept that (consecutive user turns are merged),
but some local chat templates do not.

The tail always starts with an assistant message, so the first tail message
follows a user message whatever the mode.

Gotcha for the `summarize` callable: the messages it receives may hold
tool_use / tool_result blocks, and the Anthropic API refuses those in a
request that defines no tools. Send `summary_request(messages)` instead (one
plain-text user message holding the rendered transcript and the instruction).
"""

from __future__ import annotations

import copy
import json
import os
import threading
from typing import Any, Callable, Optional

DEFAULT_CLAUDE_WINDOW = 200_000
DEFAULT_LOCAL_WINDOW = 32_768
CHARS_PER_TOKEN = 4
#: Rough per-message overhead (role markers, separators) in tokens.
MESSAGE_OVERHEAD_TOKENS = 4
#: Failed summaries before compaction gives up for the rest of the run.
MAX_FAILURES = 2

SUMMARY_INSTRUCTION = """Summarise the earlier part of a coding agent's work on a task, \
so the agent can continue without the full transcript. Write it for the agent itself. Include:
- what has been learned about the repository (relevant files, functions, how things work);
- what was changed so far (files and the nature of each edit) and why;
- commands that were run and their key results (test outcomes, errors still open);
- what was tried and did not work, so it is not repeated;
- the current state and the next steps that were planned.
Be concrete (paths, names, exact error messages) and concise. If the transcript contains an \
earlier summary, fold its content into yours. Reply with the summary only."""


def compaction_enabled() -> bool:
    """AWOS_LOOP_COMPACT (default off)."""
    return os.getenv("AWOS_LOOP_COMPACT", "").strip().lower() in ("1", "true", "yes", "on")


def context_window(model: str) -> int:
    """The model's context window in tokens. AWOS_LOOP_CONTEXT_TOKENS overrides;
    Claude models 200k (1M with the `[1m]` suffix); local / unknown 32k."""
    raw = os.getenv("AWOS_LOOP_CONTEXT_TOKENS", "").strip()
    if raw:
        try:
            value = int(float(raw))
            if value > 0:
                return value
        except ValueError:
            pass
    name = (model or "").lower()
    if "claude" in name:
        return 1_000_000 if "[1m]" in name else DEFAULT_CLAUDE_WINDOW
    return DEFAULT_LOCAL_WINDOW


def _chars(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    if isinstance(value, dict):
        return sum(_chars(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return sum(_chars(v) for v in value)
    if isinstance(value, (int, float, bool)):
        return len(str(value))
    # SDK objects or anything else: their string form is close enough.
    try:
        return len(str(value))
    except Exception:  # noqa: BLE001
        return 0


def estimate_tokens(messages: list, system: str = "") -> int:
    """Cheap estimate (~4 characters per token) for any message shape."""
    chars = _chars(system)
    count = 0
    for msg in messages or []:
        chars += _chars(msg)
        count += 1
    return -(-chars // CHARS_PER_TOKEN) + MESSAGE_OVERHEAD_TOKENS * count


# ── message-shape helpers ────────────────────────────────────────────────────


def _role(msg: Any) -> str:
    return str(msg.get("role", "")) if isinstance(msg, dict) else ""


def _tool_use_ids(msg: Any) -> set:
    """Ids of the tool calls an assistant message makes (Anthropic or OpenAI)."""
    ids: set = set()
    if not isinstance(msg, dict):
        return ids
    for call in msg.get("tool_calls") or []:
        if isinstance(call, dict) and call.get("id"):
            ids.add(call["id"])
    content = msg.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id"):
                ids.add(block["id"])
    return ids


def _tool_result_ids(msg: Any) -> set:
    """Ids of the tool calls a message answers (Anthropic or OpenAI)."""
    ids: set = set()
    if not isinstance(msg, dict):
        return ids
    if msg.get("role") == "tool" and msg.get("tool_call_id"):
        ids.add(msg["tool_call_id"])
    content = msg.get("content")
    if isinstance(content, list):
        for block in content:
            if (isinstance(block, dict) and block.get("type") == "tool_result"
                    and block.get("tool_use_id")):
                ids.add(block["tool_use_id"])
    return ids


def pairs_intact(messages: list) -> bool:
    """True when every tool result answers a tool call made earlier in `messages`."""
    seen: set = set()
    for msg in messages:
        if not _tool_result_ids(msg) <= seen:
            return False
        seen |= _tool_use_ids(msg)
    return True


def _cut_is_clean(messages: list, cut: int) -> bool:
    """Cut before index `cut`: the tail starts with an assistant turn and holds
    no result whose call is in the head (all of a head call's results stay in
    the head, so no tool_result is orphaned)."""
    if cut <= 0 or cut >= len(messages) or _role(messages[cut]) != "assistant":
        return False
    head_calls: set = set()
    for msg in messages[:cut]:
        head_calls |= _tool_use_ids(msg)
    return not any(_tool_result_ids(msg) & head_calls for msg in messages[cut:])


def render_message(msg: Any) -> str:
    """One message as plain text, whatever its shape."""
    if not isinstance(msg, dict):
        return str(msg)
    role = _role(msg) or "?"
    parts: list[str] = []
    content = msg.get("content")
    if isinstance(content, str):
        if content:
            parts.append(content)
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                parts.append(str(block))
                continue
            kind = block.get("type")
            if kind == "text":
                parts.append(str(block.get("text", "")))
            elif kind == "tool_use":
                parts.append(f"[tool call {block.get('name', '')}] "
                             f"{json.dumps(block.get('input', {}), ensure_ascii=False)}")
            elif kind == "tool_result":
                inner = block.get("content")
                if isinstance(inner, list):
                    inner = "\n".join(str(b.get("text", b)) if isinstance(b, dict) else str(b)
                                      for b in inner)
                err = " (error)" if block.get("is_error") else ""
                parts.append(f"[tool result{err}]\n{inner if inner is not None else ''}")
            else:
                parts.append(json.dumps(block, ensure_ascii=False, default=str))
    elif content is not None:
        parts.append(str(content))
    for call in msg.get("tool_calls") or []:
        fn = (call or {}).get("function") or {}
        parts.append(f"[tool call {fn.get('name', '')}] {fn.get('arguments', '')}")
    label = "tool result" if role == "tool" else role
    return f"### {label}\n" + "\n".join(parts)


def render_transcript(messages: list) -> str:
    return "\n\n".join(render_message(m) for m in messages)


def summary_request(messages: list, instruction: str = SUMMARY_INSTRUCTION) -> list[dict]:
    """A tool-free request for the summary: one user message, plain text, valid
    for every provider (no tool_use / tool_result blocks)."""
    return [{"role": "user", "content": (
        f"{instruction}\n\n<transcript>\n{render_transcript(messages)}\n</transcript>")}]


# ── the compactor ────────────────────────────────────────────────────────────


class Compactor:
    """Two-stage compaction of the loop's message list (see module docstring)."""

    def __init__(self, model: str, *, start_frac: float = 0.70, swap_frac: float = 0.85,
                 keep_recent: int = 6, summarize: Callable[[list], str],
                 merge_summary: bool = True, window: Optional[int] = None,
                 wait_s: Optional[float] = None) -> None:
        if not 0 < start_frac <= swap_frac:
            raise ValueError("need 0 < start_frac <= swap_frac")
        self.model = model
        self.window = int(window) if window else context_window(model)
        self.start_frac = start_frac
        self.swap_frac = swap_frac
        self.keep_recent = max(1, int(keep_recent))
        self._summarize = summarize
        self.merge_summary = merge_summary
        #: Max seconds maybe_swap waits for a running background summary
        #: (None: wait until it ends). On timeout the swap is retried next step.
        self.wait_s = wait_s
        self.summaries = 0
        self.failures = 0
        self.sync_runs = 0
        self.background_runs = 0
        self._files: list[str] = []
        self._goal: Optional[dict] = None
        #: The current summary as its own user message (fed to the next summary).
        self._summary_msg: Optional[dict] = None
        #: Leading messages that are goal / summary (not conversation turns).
        self._prefix_len = 1
        self._pending: Optional[dict] = None
        #: len(messages) right after the last swap: nothing runs until the list
        #: grows past it (used_tokens of that step predates the swap).
        self._swapped_len: Optional[int] = None
        #: After a failure, no new attempt before the list reaches this length.
        self._retry_len = 0
        self.last_cut: Optional[int] = None
        self.tokens_before: Optional[int] = None
        self.tokens_after: Optional[int] = None
        self.history: list[dict] = []

    # -- public --

    def note_files(self, files: list[str]) -> None:
        for f in files or []:
            f = str(f)
            if f and f not in self._files:
                self._files.append(f)

    def maybe_start(self, messages: list, used_tokens: int) -> None:
        """At >= start_frac, start the background summary (once per compaction)."""
        try:
            if not self._may_act(messages, used_tokens, self.start_frac):
                return
            if self._pending is not None:
                return
            plan = self._plan(messages)
            if plan is None:
                return
            self._launch(plan)
        except Exception:  # noqa: BLE001 - compaction never breaks the loop
            return

    def maybe_swap(self, messages: list, used_tokens: int) -> Optional[list]:
        """At >= swap_frac, the compacted message list; else None. Never raises."""
        try:
            return self._maybe_swap(messages, used_tokens)
        except Exception:  # noqa: BLE001
            self._fail(messages)
            return None

    def summary(self) -> dict:
        return {"enabled": True, "window": self.window, "start_frac": self.start_frac,
                "swap_frac": self.swap_frac, "keep_recent": self.keep_recent,
                "summaries": self.summaries, "failures": self.failures,
                "background_runs": self.background_runs, "sync_runs": self.sync_runs,
                "last_cut": self.last_cut, "tokens_before": self.tokens_before,
                "tokens_after": self.tokens_after, "files": list(self._files),
                "history": [dict(h) for h in self.history]}

    # -- internals --

    def _used(self, messages: list, used_tokens: Any) -> int:
        try:
            used = int(used_tokens or 0)
        except (TypeError, ValueError):
            used = 0
        return used if used > 0 else estimate_tokens(messages)

    def _may_act(self, messages: list, used_tokens: Any, frac: float) -> bool:
        if not messages or self.failures >= MAX_FAILURES:
            return False
        if len(messages) < self._retry_len:
            return False
        if self._swapped_len is not None and len(messages) <= self._swapped_len:
            return False
        return self._used(messages, used_tokens) >= frac * self.window

    def _plan(self, messages: list) -> Optional[dict]:
        """Where to cut: before the keep_recent-th last assistant turn, moved
        earlier until no tool pair is split. None when there is too little to
        summarise."""
        if self._goal is None:
            self._goal = copy.deepcopy(messages[0])
        first = self._prefix_len
        assistants = [i for i in range(first + 1, len(messages))
                      if _role(messages[i]) == "assistant"]
        if len(assistants) < self.keep_recent:
            return None
        candidates = assistants[:len(assistants) - self.keep_recent + 1]
        for cut in reversed(candidates):
            if cut - first < 2:  # at least one turn of conversation to summarise
                break
            if _cut_is_clean(messages, cut):
                head = list(messages[first:cut])
                if self._summary_msg is not None:
                    # The earlier summary is part of what gets summarised.
                    head = [self._summary_msg] + head
                return {"cut": cut, "anchor": messages[cut],
                        "head": copy.deepcopy(head), "result": None, "error": None,
                        "thread": None}
        return None

    def _run(self, plan: dict, *, background: bool = False) -> None:
        catch = BaseException if background else Exception
        try:
            text = self._summarize(plan["head"])
            text = text.strip() if isinstance(text, str) else ""
            if not text:
                raise ValueError("empty summary")
            plan["result"] = text
        except catch as exc:  # noqa: BLE001 - reported via plan["error"]
            plan["error"] = exc

    def _launch(self, plan: dict) -> None:
        thread = threading.Thread(target=self._run, args=(plan,), kwargs={"background": True},
                                  daemon=True,
                                  name="awos-loop-compaction")
        plan["thread"] = thread
        self._pending = plan
        self.background_runs += 1
        thread.start()

    def _fail(self, messages: list) -> None:
        self.failures += 1
        self._pending = None
        # Wait for a few more turns before trying again (never every step).
        self._retry_len = len(messages or []) + 2 * self.keep_recent

    def _maybe_swap(self, messages: list, used_tokens: Any) -> Optional[list]:
        if not self._may_act(messages, used_tokens, self.swap_frac):
            return None
        plan = self._pending
        if plan is not None:
            cut = plan["cut"]
            if cut >= len(messages) or messages[cut] is not plan["anchor"]:
                # The list was rewritten since the summary started: start over.
                if plan["thread"] is not None and plan["thread"].is_alive():
                    plan["thread"].join(self.wait_s)
                plan = None
                self._pending = None
        if plan is None:
            plan = self._plan(messages)
            if plan is None:
                return None
            self.sync_runs += 1
            self._run(plan)
        elif plan["thread"] is not None and plan["thread"].is_alive():
            plan["thread"].join(self.wait_s)
            if plan["thread"].is_alive():
                return None  # still running: try again next step
        if plan["error"] is not None or not plan["result"]:
            self._fail(messages)
            return None
        return self._swap(messages, plan, self._used(messages, used_tokens))

    def _summary_text(self, text: str, number: int) -> str:
        files = ", ".join(self._files) if self._files else "(none recorded)"
        return (f"<summary number=\"{number}\">\n{text}\n</summary>\n\n"
                f"This is summary number {number} of earlier work on this task: the "
                "conversation before this point was replaced by the summary above to save "
                "context. The task is unchanged; continue from where the work stands.\n"
                f"Files edited so far: {files}")

    def _swap(self, messages: list, plan: dict, used: int) -> list:
        number = self.summaries + 1
        body = self._summary_text(plan["result"], number)
        summary_msg = {"role": "user", "content": body}
        goal = copy.deepcopy(self._goal) if self._goal is not None else copy.deepcopy(messages[0])
        tail = list(messages[plan["cut"]:])
        if self.merge_summary:
            head = [_append_text(goal, body)]
        else:
            head = [goal, summary_msg]
        new = head + tail
        self.summaries = number
        self._summary_msg = summary_msg
        self._prefix_len = len(head)
        self._pending = None
        self._swapped_len = len(new)
        self.last_cut = plan["cut"]
        self.tokens_before = used
        self.tokens_after = estimate_tokens(new)
        self.history.append({"summary": number, "cut": plan["cut"],
                             "messages_before": len(messages), "messages_after": len(new),
                             "tokens_before": used, "tokens_after": self.tokens_after,
                             "summary_chars": len(plan["result"])})
        return new


def _append_text(msg: dict, text: str) -> dict:
    """A copy of user message `msg` with `text` appended after its content."""
    out = dict(msg)
    content = out.get("content")
    sep = "\n\n---\n\n"
    if isinstance(content, list):
        out["content"] = list(content) + [{"type": "text", "text": sep.lstrip() + text}]
    else:
        out["content"] = f"{content or ''}{sep}{text}"
    return out


__all__ = ["Compactor", "SUMMARY_INSTRUCTION", "compaction_enabled", "context_window",
           "estimate_tokens", "pairs_intact", "render_message", "render_transcript",
           "summary_request"]
