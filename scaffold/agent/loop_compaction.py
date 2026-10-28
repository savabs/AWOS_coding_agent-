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

Robustness (what the loop executor turns on; the defaults of the class keep
the plain behaviour above for direct callers):
  * thresholds with a reserve (`reserve_tokens`): the caller passes the
    estimated size of the NEXT request (next_request_tokens: last input +
    last output + what was appended since), and the swap threshold is
    min(swap_frac * window, window - reserve), where the reserve covers one
    reply (the output cap), one maximal observation and a margin; the start
    threshold keeps its ratio to it. The reserve is capped at half the
    window, so a tiny window still compacts at a sane point;
  * a budgeted summary request: summary_request(..., budget_chars=N) gives
    every message a fair share of N characters, cuts long ones in the middle
    and keeps the lines that say what was run (tool calls, return codes);
  * `deterministic_fallback`: when the summary call fails or returns
    nothing, a summary built from the messages themselves (commands with
    their exit codes, files edited, the last error) is used instead; it
    cannot fail, so the swap still happens (`fallbacks` counts these);
  * note_gate_feedback(): the latest test-gate feedback is kept verbatim
    next to the goal when the swap would otherwise drop it;
  * force(): a synchronous swap regardless of thresholds (the executor's
    context-overflow recovery);
  * restore(): picks the summary count and the goal back up from a resumed
    conversation.
"""

from __future__ import annotations

import copy
import json
import os
import re
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


def next_request_tokens(last_input: int, last_output: int, appended: list) -> int:
    """Estimated size of the next request: the last request's input, plus the
    reply it produced, plus what has been appended since (tool results,
    notes; pass them without the reply's own assistant turn). 0 when the
    last input is unknown (the caller then falls back to an estimate of the
    whole list)."""
    try:
        last_input, last_output = int(last_input or 0), int(last_output or 0)
    except (TypeError, ValueError):
        return 0
    if last_input <= 0:
        return 0
    return last_input + max(0, last_output) + (estimate_tokens(appended) if appended else 0)


#: Lines of a rendered message that say what was run and how it ended; kept
#: when the message is cut.
_KEY_LINE = re.compile(r"^(### |\[tool call|\[tool result)|<returncode>")


def _fair_caps(lengths: list, budget: int) -> list:
    """Per-item character caps summing to <= budget: short items keep their
    length, the long ones share what is left equally (water filling)."""
    caps = list(lengths)
    if sum(lengths) <= budget:
        return caps
    remaining, left = max(0, budget), len(lengths)
    for i in sorted(range(len(lengths)), key=lambda k: lengths[k]):
        share = remaining // left if left else 0
        caps[i] = min(lengths[i], share)
        remaining -= caps[i]
        left -= 1
    return caps


def _cut_middle(text: str, cap: int) -> str:
    """`text` within `cap` characters: head + tail with a notice, and any key
    line (tool call, return code) from the cut part put back up front."""
    if len(text) <= cap:
        return text
    keys: list[str] = []
    for line in text.splitlines():
        if _KEY_LINE.search(line):
            line = line[:300]
            if line not in keys:
                keys.append(line)
    notice = f"\n[... {len(text) - cap} characters cut from the middle ...]\n"
    essential = "\n".join(keys)
    room = cap - len(notice) - len(essential) - 1
    if room < 80:
        return (essential or text[:cap])[:max(cap, 80)]
    half = room // 2
    head, tail = text[:half], text[len(text) - half:]
    missing = [k for k in keys if k not in head and k not in tail]
    lead = ("\n".join(missing) + "\n") if missing else ""
    return f"{lead}{head}{notice}{tail}"


def summary_request(messages: list, instruction: str = SUMMARY_INSTRUCTION,
                    budget_chars: Optional[int] = None) -> list[dict]:
    """A tool-free request for the summary: one user message, plain text, valid
    for every provider (no tool_use / tool_result blocks).

    budget_chars: the most characters the transcript may take; each message
    gets a fair share (see _fair_caps) and long ones are cut in the middle
    with their commands and return codes kept."""
    rendered = [render_message(m) for m in messages]
    if budget_chars is not None and budget_chars > 0:
        sep = 2 * max(0, len(rendered) - 1)
        caps = _fair_caps([len(r) for r in rendered], max(0, int(budget_chars) - sep))
        rendered = [_cut_middle(r, c) for r, c in zip(rendered, caps)]
    transcript = "\n\n".join(rendered)
    return [{"role": "user", "content": (
        f"{instruction}\n\n<transcript>\n{transcript}\n</transcript>")}]


# ── deterministic summary (the fallback that cannot fail) ───────────────────


_RC = re.compile(r"<returncode>(-?\d+)</returncode>")
_BASH_BLOCK = re.compile(r"```(?:bash|sh)?\s*\n(.*?)```", re.S)
_TAGS = re.compile(r"<(returncode|warning)>.*?</\1>|</?output>", re.S)


def _block_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text", "")) if isinstance(b, dict) else str(b)
                         for b in content)
    return "" if content is None else str(content)


def _commands_and_results(messages: list) -> list:
    """[(command, exit code or None, output text)] in order, for every shape."""
    out: list = []
    pending: dict = {}  # tool call id -> index in out
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role, content = msg.get("role"), msg.get("content")
        if role == "assistant":
            found = False
            for call in msg.get("tool_calls") or []:
                fn = (call or {}).get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (TypeError, ValueError):
                    args = {}
                cmd = args.get("command") if isinstance(args, dict) else None
                pending[(call or {}).get("id")] = len(out)
                out.append([str(cmd if cmd is not None else fn.get("arguments", "")), None, ""])
                found = True
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        inp = b.get("input") or {}
                        cmd = inp.get("command") if isinstance(inp, dict) else inp
                        pending[b.get("id")] = len(out)
                        out.append([str(cmd), None, ""])
                        found = True
            if not found and isinstance(content, str):
                m = _BASH_BLOCK.search(content)
                if m:
                    pending["__text__"] = len(out)
                    out.append([m.group(1).strip(), None, ""])
            continue
        results: list = []
        if role == "tool":
            results.append((msg.get("tool_call_id"), _block_text(content)))
        elif isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    results.append((b.get("tool_use_id"), _block_text(b.get("content"))))
        elif isinstance(content, str) and "__text__" in pending and _RC.search(content):
            results.append(("__text__", content))
        for rid, text in results:
            idx = pending.pop(rid, None)
            if idx is None:
                continue
            m = _RC.search(text or "")
            out[idx][1] = int(m.group(1)) if m else None
            out[idx][2] = text or ""
    return [tuple(x) for x in out]


def _last_line(text: str) -> str:
    lines = [ln.strip() for ln in _TAGS.sub("", text or "").splitlines()]
    lines = [ln for ln in lines if ln and not set(ln) <= set("-=_*~^ ")]
    return lines[-1][:300] if lines else ""


def deterministic_summary(messages: list, files: Optional[list] = None,
                          max_commands: int = 40) -> str:
    """A summary built from the messages alone (no model): the commands run
    with their exit codes, the files edited, the last error. Never raises."""
    try:
        runs = _commands_and_results(messages)
        lines = ["(Automatic summary: the model summary was unavailable, so this lists "
                 "what was run.)"]
        earlier = ""
        for msg in messages[:1]:
            text = _block_text(msg.get("content") if isinstance(msg, dict) else msg)
            if "<summary number=" in text:
                earlier = text
        if earlier:
            lines.append("Earlier summary (shortened):\n" + _cut_middle(earlier, 3000))
        if runs:
            skipped = max(0, len(runs) - max_commands)
            lines.append(f"Commands run ({len(runs)}"
                         + (f", the first {skipped} not listed" if skipped else "") + "):")
            for cmd, code, _ in runs[skipped:]:
                one = " ".join(str(cmd).split())
                one = one if len(one) <= 200 else one[:197] + "..."
                lines.append(f"- `{one}` -> exit {code if code is not None else '?'}")
        else:
            lines.append("No commands were run in this part.")
        lines.append("Files edited: " + (", ".join(str(f) for f in files)
                                         if files else "(none recorded)"))
        last_err = next(((cmd, code, out) for cmd, code, out in reversed(runs)
                         if code not in (0, None)), None)
        if last_err is not None:
            sig = _last_line(last_err[2]) or f"exit {last_err[1]}"
            lines.append(f"Last error (from `{' '.join(str(last_err[0]).split())[:120]}`): {sig}")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001 - the fallback must not fail
        return ("(Automatic summary: earlier work could not be summarised.) Files edited: "
                + ", ".join(str(f) for f in (files or [])))


# ── the compactor ────────────────────────────────────────────────────────────


class Compactor:
    """Two-stage compaction of the loop's message list (see module docstring)."""

    def __init__(self, model: str, *, start_frac: float = 0.70, swap_frac: float = 0.85,
                 keep_recent: int = 6, summarize: Callable[[list], str],
                 merge_summary: bool = True, window: Optional[int] = None,
                 wait_s: Optional[float] = None, reserve_tokens: int = 0,
                 deterministic_fallback: bool = False) -> None:
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
        #: Tokens kept free below the window (next reply + one observation +
        #: margin); capped at half the window. 0: plain fractions.
        self.reserve_tokens = max(0, min(int(reserve_tokens or 0), self.window // 2))
        #: Use deterministic_summary when the summary call fails.
        self.deterministic_fallback = bool(deterministic_fallback)
        self.fallbacks = 0
        self.forced = 0
        self._gate_feedback: Optional[str] = None
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

    def note_gate_feedback(self, text: str) -> None:
        """The latest test-gate feedback, kept verbatim next to the goal."""
        if isinstance(text, str) and text.strip():
            self._gate_feedback = text.strip()

    def threshold(self, frac: float) -> float:
        """Token count at which the stage with fraction `frac` acts."""
        swap = self.swap_frac * self.window
        if self.reserve_tokens:
            swap = min(swap, self.window - self.reserve_tokens)
        return swap * (frac / self.swap_frac)

    def restore(self, messages: list, summaries: int) -> None:
        """Continue a resumed conversation: its first message may already hold
        summary number `summaries` merged after the goal. Never raises."""
        try:
            n = int(summaries or 0)
            if n <= 0 or not messages:
                return
            self.summaries = n
            first = messages[0]
            content = first.get("content") if isinstance(first, dict) else None
            mark = "<summary number="
            body, goal = None, None
            if isinstance(content, str):
                cut = content.rfind(_SEP + mark)
                if cut >= 0:
                    goal = dict(first, content=content[:cut])
                    body = content[cut + len(_SEP):]
            elif isinstance(content, list) and content:
                last = content[-1]
                text = last.get("text", "") if isinstance(last, dict) else ""
                lead = _SEP.lstrip()
                if isinstance(text, str) and text.startswith(lead + mark):
                    goal = dict(first, content=list(content[:-1]))
                    body = text[len(lead):]
            if goal is not None and body is not None:
                self._goal = copy.deepcopy(goal)
                self._summary_msg = {"role": "user", "content": body}
        except Exception:  # noqa: BLE001
            return

    def force(self, messages: list) -> Optional[list]:
        """Swap now, synchronously, whatever the thresholds (context overflow
        recovery). Tries keep_recent, then fewer recent turns down to one.
        The compacted list, or None when there is nothing to summarise or the
        summary failed (and no fallback). Never raises."""
        try:
            # A background summary still running is left to finish unused.
            self._pending = None
            plan = None
            for keep in range(self.keep_recent, 0, -1):
                plan = self._plan(messages, keep)
                if plan is not None:
                    break
            if plan is None:
                return None
            self.sync_runs += 1
            self.forced += 1
            self._run(plan)
            if plan["error"] is not None or not plan["result"]:
                self._fail(messages)
                return None
            return self._swap(messages, plan, estimate_tokens(messages))
        except Exception:  # noqa: BLE001
            return None

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
                "reserve_tokens": self.reserve_tokens,
                "summaries": self.summaries, "failures": self.failures,
                "fallbacks": self.fallbacks, "forced": self.forced,
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
        return self._used(messages, used_tokens) >= self.threshold(frac)

    def _plan(self, messages: list, keep_recent: Optional[int] = None) -> Optional[dict]:
        """Where to cut: before the keep_recent-th last assistant turn, moved
        earlier until no tool pair is split. None when there is too little to
        summarise."""
        keep = self.keep_recent if keep_recent is None else max(1, int(keep_recent))
        if self._goal is None:
            self._goal = copy.deepcopy(messages[0])
        first = self._prefix_len
        assistants = [i for i in range(first + 1, len(messages))
                      if _role(messages[i]) == "assistant"]
        if len(assistants) < keep:
            return None
        candidates = assistants[:len(assistants) - keep + 1]
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
        if plan.get("result") is None and self.deterministic_fallback:
            plan["result"] = deterministic_summary(plan["head"], list(self._files))
            plan["error"] = None
            plan["fallback"] = True

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

    def _summary_text(self, text: str, number: int, feedback: Optional[str] = None) -> str:
        files = ", ".join(self._files) if self._files else "(none recorded)"
        out = (f"<summary number=\"{number}\">\n{text}\n</summary>\n\n"
               f"This is summary number {number} of earlier work on this task: the "
               "conversation before this point was replaced by the summary above to save "
               "context. The task is unchanged; continue from where the work stands.\n"
               f"Files edited so far: {files}")
        if feedback:
            out += ("\n\nThe latest feedback from AWOS's test gate on your last submit "
                    f"(verbatim):\n{feedback}")
        return out

    def _swap(self, messages: list, plan: dict, used: int) -> list:
        number = self.summaries + 1
        feedback = self._gate_feedback
        if feedback and any(feedback in render_message(m) for m in messages[plan["cut"]:]):
            feedback = None  # still in the recent turns, verbatim
        body = self._summary_text(plan["result"], number, feedback)
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
                             "summary_chars": len(plan["result"]),
                             **({"fallback": True} if plan.get("fallback") else {})})
        if plan.get("fallback"):
            self.fallbacks += 1
        return new


_SEP = "\n\n---\n\n"


def _append_text(msg: dict, text: str) -> dict:
    """A copy of user message `msg` with `text` appended after its content."""
    out = dict(msg)
    content = out.get("content")
    sep = _SEP
    if isinstance(content, list):
        out["content"] = list(content) + [{"type": "text", "text": sep.lstrip() + text}]
    else:
        out["content"] = f"{content or ''}{sep}{text}"
    return out


__all__ = ["Compactor", "SUMMARY_INSTRUCTION", "compaction_enabled", "context_window",
           "deterministic_summary", "estimate_tokens", "next_request_tokens", "pairs_intact",
           "render_message", "render_transcript", "summary_request"]
