"""
loop_stuck.py — a behavioural stuck detector for the `loop` executor
(AWOS_LOOP_STUCK=off|log|nudge, default log).

Why: docs/research/rea_synthesis_2026-10-16.md item 7. A model in an open
bash loop can spin: the same command again and again, most commands failing,
the same error coming back after every "fix", or many steps with no change to
the workspace. Commercial harnesses notice this and say so. This module only
watches what the model DOES (commands, exit status, output, workspace state);
it never counts phrases or hedging in the model's text.

HARD RULE: the detector never stops a run. Early stopping is what lost 33/40
on a past benchmark. It can only:
  * nudge: return a short, trigger-specific message suggesting a different
    approach (mode "nudge" only), appended by the caller as a user message;
  * recommend escalation: set `escalate_recommended` once a trigger has
    persisted `escalate_after` steps past its nudge. Acting on that (a
    stronger model, a human) is the caller's decision; the run continues.
Nudge texts never tell the model to stop, give up or submit.

Modes:
  off    nothing is tracked, observe() returns None;
  log    (default) triggers are detected and counted, the prompt is unchanged;
         summary() says how often each trigger fired and whether escalation
         would have been recommended, so the lift can be measured before the
         nudges are switched on;
  nudge  as log, plus observe() returns the nudge text when a trigger fires.

Triggers (names are stable; they appear in summaries and telemetry):
  repeat_command     the same normalised command run >= repeat_threshold times
                     in a row, or the same command with the same output
                     signature >= repeat_threshold times within the last
                     `window` commands (catches A, B, A, B, A loops);
  failure_rate       >= fail_threshold of the last `window` tool results
                     failed (only once the window is full);
  failure_signature  the same failure signature (last meaningful output
                     line, digits and hex stripped) in >= repeat_threshold
                     consecutive failing results;
  no_progress        the caller's progress_key unchanged for
                     no_progress_steps observed steps (only once a key has
                     been seen; None means "unknown" and is ignored).

Step accounting: cooldowns and escalation count observe() calls (steps that
ran commands), not the caller's step numbers, so gaps or odd step values do
not matter. The caller's step is recorded in first_fire_step.

A trigger "fires" when it becomes active (inactive -> active). While it stays
active it is not nudged again; a fire suppressed by the per-trigger cooldown
(window // 2 steps since that trigger's last nudge) is nudged once the
cooldown has passed if it is still active.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections import Counter, deque
from itertools import zip_longest
from typing import Any, Optional

MODES = ("off", "log", "nudge")
DEFAULT_MODE = "log"

TRIGGERS = ("repeat_command", "failure_rate", "failure_signature", "no_progress")

#: Characters of a command or error line quoted back in a nudge.
QUOTE_LIMIT = 120
NUDGE_PREFIX = "[harness note]"


def stuck_mode() -> str:
    """AWOS_LOOP_STUCK: off | log | nudge (default log; anything else -> log)."""
    raw = (os.getenv("AWOS_LOOP_STUCK", "") or "").strip().lower()
    return raw if raw in MODES else DEFAULT_MODE


# ── normalisation (pure) ─────────────────────────────────────────────────────

_WS = re.compile(r"\s+")
_HEX = re.compile(r"0x[0-9a-fA-F]+")
_DIGITS = re.compile(r"\d+")
_TAG_BLOCK = re.compile(r"<(returncode|warning)>.*?</\1>", re.S)
_TAG = re.compile(r"</?output>")


def normalize_command(command: Any) -> str:
    """Whitespace-collapsed command string ('' for anything unusable)."""
    if command is None:
        return ""
    if not isinstance(command, str):
        command = str(command)
    return _WS.sub(" ", command).strip()


def _scrub(text: str) -> str:
    text = _HEX.sub("0x#", text)
    text = _DIGITS.sub("#", text)
    return _WS.sub(" ", text).strip()


def _strip_envelope(text: str) -> str:
    """The command output without loop_executor's <returncode>/<warning>/<output> tags."""
    return _TAG.sub("", _TAG_BLOCK.sub("", text))


def _result_text(result: Any) -> str:
    text = getattr(result, "text", "") or ""
    if not text:
        text = getattr(result, "error", "") or ""
    return text if isinstance(text, str) else str(text)


def _result_data(result: Any) -> dict:
    data = getattr(result, "data", None)
    return data if isinstance(data, dict) else {}


def result_failed(result: Any) -> Optional[bool]:
    """True / False, or None when the result says nothing either way."""
    if result is None:
        return None
    success = getattr(result, "success", None)
    if isinstance(success, bool):
        return not success
    data = _result_data(result)
    if data.get("timed_out"):
        return True
    code = data.get("exit_code")
    if isinstance(code, int) and not isinstance(code, bool):
        return code != 0
    return None


def output_signature(result: Any) -> str:
    """Short hash of the scrubbed output (digits/hex stripped, so timings and
    addresses do not make identical runs look different)."""
    body = _scrub(_strip_envelope(_result_text(result)))
    return hashlib.sha1(body.encode("utf-8", "replace")).hexdigest()[:12]


def failure_signature(result: Any) -> str:
    """The last meaningful output line, scrubbed; falls back to timeout/exit code."""
    data = _result_data(result)
    lines = [ln.strip() for ln in _strip_envelope(_result_text(result)).splitlines()]
    lines = [ln for ln in lines if ln and not set(ln) <= set("-=_*~^ ")]
    if lines:
        return _scrub(lines[-1])[:200]
    if data.get("timed_out"):
        return "<timed out>"
    return f"<exit {data.get('exit_code', '?')}>"


def _quote(text: str) -> str:
    text = text.replace("`", "'")
    return text if len(text) <= QUOTE_LIMIT else text[:QUOTE_LIMIT - 3] + "..."


def nudge_text(trigger: str, detail: dict) -> str:
    """Short, trigger-specific suggestion. Never says stop, give up or submit."""
    if trigger == "repeat_command":
        return (f"{NUDGE_PREFIX} You have run `{_quote(detail.get('command', ''))}` "
                f"{detail.get('count', 'several')} times without new information; its "
                "output will not change unless something else does. Try a different "
                "approach: read the code involved, make a change, or test a narrower "
                "hypothesis.")
    if trigger == "failure_rate":
        return (f"{NUDGE_PREFIX} {detail.get('failed', 'Most')} of your last "
                f"{detail.get('window', 'few')} commands failed. Re-read the error "
                "output and check your assumptions (paths, working directory, installed "
                "tools, file contents) before the next command; try a different approach "
                "rather than variations of the same one.")
    if trigger == "failure_signature":
        return (f"{NUDGE_PREFIX} The same error has come back {detail.get('count', 'several')} "
                f"times in a row: `{_quote(detail.get('signature', ''))}`. The current line "
                "of attack is not reaching its cause. Find where that error is raised, form "
                "a new hypothesis, and try a different fix.")
    if trigger == "no_progress":
        return (f"{NUDGE_PREFIX} The workspace has not changed in the last "
                f"{detail.get('steps', 'several')} steps. If you know what to change, make "
                "the edit; if not, look at a different part of the code or reproduce the "
                "problem with a smaller example to find the cause.")
    return f"{NUDGE_PREFIX} You seem to be repeating yourself; try a different approach."


# ── the detector ─────────────────────────────────────────────────────────────


class _TriggerState:
    __slots__ = ("active", "fires", "first_step", "nudged_this_activation",
                 "last_nudge_tick", "nudge_tick")

    def __init__(self) -> None:
        self.active = False
        self.fires = 0
        self.first_step: Any = None
        self.nudged_this_activation = False
        self.last_nudge_tick: Optional[int] = None  # for the cooldown
        self.nudge_tick: Optional[int] = None  # nudge of the current activation


def _pos_int(value: Any, default: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


class StuckDetector:
    """Watches a loop's commands and results; nudges, then recommends escalation.

    Never stops the run and never raises from observe()/summary().
    """

    def __init__(self, mode: Optional[str] = None, *, window: int = 20, fail_threshold: int = 15,
                 repeat_threshold: int = 3, no_progress_steps: int = 12,
                 escalate_after: int = 4) -> None:
        mode = (mode or "").strip().lower() if isinstance(mode, str) else None
        self.mode = mode if mode in MODES else stuck_mode()
        self.window = _pos_int(window, 20)
        self.fail_threshold = min(self.window, _pos_int(fail_threshold, 15))
        self.repeat_threshold = max(2, _pos_int(repeat_threshold, 3))
        self.no_progress_steps = _pos_int(no_progress_steps, 12)
        self.escalate_after = _pos_int(escalate_after, 4)
        self.cooldown = max(1, self.window // 2)

        self.escalate_recommended = False
        self.escalate_step: Any = None
        self.escalate_trigger: Optional[str] = None
        self.nudges = 0  # nudge texts returned (mode nudge)
        self.would_nudge = 0  # nudge points in any non-off mode
        self.errors = 0  # swallowed internal errors

        self._tick = 0
        self._states = {name: _TriggerState() for name in TRIGGERS}
        self._fails: deque = deque(maxlen=self.window)
        self._recent_pairs: deque = deque(maxlen=self.window)
        self._last_cmd: Optional[str] = None
        self._cmd_run = 0
        self._last_fail_sig: Optional[str] = None
        self._fail_sig_run = 0
        self._progress_key: Any = None
        self._seen_key = False
        self._unchanged = 0

    # -- public ---------------------------------------------------------------

    def observe(self, step: int, commands: list, results: list,
                progress_key: object = None) -> Optional[str]:
        """Feed one step. Returns nudge text (mode nudge, newly fired trigger) or None."""
        if self.mode == "off":
            return None
        try:
            return self._observe(step, commands, results, progress_key)
        except Exception:  # never let the detector break a run
            self.errors += 1
            return None

    def summary(self) -> dict:
        try:
            return {
                "mode": self.mode,
                "triggers": {name: st.fires for name, st in self._states.items()},
                "nudges": self.nudges,
                "would_nudge": self.would_nudge,
                "escalate_recommended": self.escalate_recommended,
                "escalate_step": self.escalate_step,
                "escalate_trigger": self.escalate_trigger,
                "first_fire_step": {name: st.first_step for name, st in self._states.items()
                                    if st.fires},
                "steps_observed": self._tick,
                "errors": self.errors,
            }
        except Exception:
            return {"mode": getattr(self, "mode", "?"), "triggers": {}, "nudges": 0,
                    "escalate_recommended": False, "first_fire_step": {}}

    # -- internals ------------------------------------------------------------

    @staticmethod
    def _as_list(value: Any) -> list:
        if value is None or isinstance(value, (str, bytes)):
            return [value] if isinstance(value, str) else []
        try:
            return list(value)
        except TypeError:
            return [value]

    def _observe(self, step: Any, commands: Any, results: Any,
                 progress_key: Any) -> Optional[str]:
        self._tick += 1
        hit: dict[str, dict] = {}

        for command, result in zip_longest(self._as_list(commands), self._as_list(results)):
            self._feed_one(command, result, hit)

        if self._fails.maxlen and len(self._fails) == self._fails.maxlen:
            failed = sum(self._fails)
            if failed >= self.fail_threshold:
                hit["failure_rate"] = {"failed": failed, "window": self.window}

        self._feed_progress(progress_key, hit)
        return self._update_triggers(step, hit)

    def _feed_one(self, command: Any, result: Any, hit: dict) -> None:
        if command is None and result is None:
            return
        cmd = normalize_command(command) if command is not None else None

        if cmd is not None:
            if cmd == self._last_cmd:
                self._cmd_run += 1
            else:
                self._last_cmd, self._cmd_run = cmd, 1
            if cmd and self._cmd_run >= self.repeat_threshold:
                hit["repeat_command"] = {"command": cmd, "count": self._cmd_run}
            if cmd and result is not None:
                pair = (cmd, output_signature(result))
                self._recent_pairs.append(pair)
                count = Counter(self._recent_pairs)[pair]
                if count >= self.repeat_threshold and "repeat_command" not in hit:
                    hit["repeat_command"] = {"command": cmd, "count": count}

        if result is None:
            return
        failed = result_failed(result)
        if failed is None:
            return
        self._fails.append(1 if failed else 0)
        if failed:
            sig = failure_signature(result)
            if sig == self._last_fail_sig:
                self._fail_sig_run += 1
            else:
                self._last_fail_sig, self._fail_sig_run = sig, 1
            if self._fail_sig_run >= self.repeat_threshold:
                hit["failure_signature"] = {"signature": sig, "count": self._fail_sig_run}
        else:
            self._last_fail_sig, self._fail_sig_run = None, 0

    def _feed_progress(self, key: Any, hit: dict) -> None:
        if key is None:
            return
        try:
            hash(key)
        except TypeError:
            key = repr(key)
        if not self._seen_key:
            self._seen_key, self._progress_key, self._unchanged = True, key, 0
            return
        try:
            same = bool(key == self._progress_key)
        except Exception:
            same = False
        if same:
            self._unchanged += 1
        else:
            self._progress_key, self._unchanged = key, 0
        if self._unchanged >= self.no_progress_steps:
            hit["no_progress"] = {"steps": self._unchanged}

    def _update_triggers(self, step: Any, hit: dict) -> Optional[str]:
        texts = []
        for name in TRIGGERS:
            st = self._states[name]
            if name not in hit:
                st.active = False
                st.nudged_this_activation = False
                st.nudge_tick = None
                continue
            if not st.active:  # a new fire
                st.active = True
                st.fires += 1
                if st.first_step is None:
                    st.first_step = step
            if not st.nudged_this_activation and (
                    st.last_nudge_tick is None or self._tick - st.last_nudge_tick >= self.cooldown):
                st.nudged_this_activation = True
                st.last_nudge_tick = st.nudge_tick = self._tick
                self.would_nudge += 1
                if self.mode == "nudge":
                    self.nudges += 1
                    texts.append(nudge_text(name, hit[name]))
            elif (st.nudge_tick is not None and not self.escalate_recommended
                  and self._tick - st.nudge_tick >= self.escalate_after):
                self.escalate_recommended = True
                self.escalate_step = step
                self.escalate_trigger = name
        return "\n\n".join(texts) if texts else None
