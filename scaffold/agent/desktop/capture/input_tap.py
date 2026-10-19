# SPDX-License-Identifier: MIT
# Portions adapted from openadapt-capture, openadapt_capture/input_observer/darwin.py
# (keycode table, permission preflight, tap-disabled recovery, _is_injected) and
# input_observer/base.py (observed-event shapes).
# Copyright (c) 2025-2026 OpenAdapt.AI. MIT License: see ./NOTICE.
# AWOS changes: kCGEventTapOptionListenOnly instead of the active-filter tap
# (the recorder can never delay or alter the user's input); no permission
# REQUEST (preflight only); no delivery-barrier/receipt machinery; character
# keys are never decoded (no CGEventKeyboardGetUnicodeString) so typed text,
# including passwords, cannot reach a record; output is a plain dict record.
"""
input_tap.py — a listen-only Quartz CGEventTap for clicks and a few named keys.

    tap = ListenOnlyTap(callback)              # callback(record: dict)
    tap.schedule(run_loop)                     # adds the tap's source to a CFRunLoop
    ...
    tap.close()

Records:
    {"src": "mouse", "button": "left", "pressed": True, "x": 812.0, "y": 340.5,
     "injected": False, "t": ...}
    {"src": "key", "key": "enter", "pressed": True, "injected": False, "t": ...}

Only named, non-character keys are reported (enter, tab, esc, arrows ...);
any key that would produce text is dropped at the tap. Typed text is captured
from AXValueChanged instead, where secure fields are omitted. "injected" is
True when the event came from another process (CGEventPost/automation; source
PID > 0) — the adapter drops those, so AWOS's own actions are not recorded as
the user's, and they double as a user-takeover signal elsewhere.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

# keycodes -> names (subset of the openadapt-capture darwin table: keys that never produce text)
KEY_NAMES = {
    36: "enter", 48: "tab", 53: "esc", 76: "enter", 51: "backspace", 117: "delete",
    123: "left", 124: "right", 125: "down", 126: "up", 115: "home", 119: "end",
    116: "page_up", 121: "page_down",
}
REPORTED_KEYS = frozenset({"enter", "tab", "esc"})


class TapUnavailable(RuntimeError):
    pass


class ListenOnlyTap:
    def __init__(self, callback: Callable[[dict], None], *, frameworks: Optional[Dict[str, Any]] = None,
                 clock: Callable[[], float] = time.time):
        if frameworks is None:
            from .permissions import load_frameworks
            frameworks = load_frameworks(("Quartz",))
        self.q = frameworks["Quartz"]
        self.callback = callback
        self.clock = clock
        self._tap = None
        self._source = None
        self._loop = None
        self._cb_ref = self._on_event
        self.disabled_count = 0

    # ------------------------------------------------------------- setup --
    def event_mask(self) -> int:
        q = self.q
        m = 0
        for t in (q.kCGEventLeftMouseDown, q.kCGEventRightMouseDown, q.kCGEventKeyDown):
            m |= q.CGEventMaskBit(t)
        return m

    def schedule(self, run_loop: Any) -> None:
        q = self.q
        self._tap = q.CGEventTapCreate(q.kCGSessionEventTap, q.kCGHeadInsertEventTap,
                                       q.kCGEventTapOptionListenOnly, self.event_mask(), self._cb_ref, None)
        if self._tap is None:
            raise TapUnavailable("CGEventTapCreate returned NULL: Input Monitoring is not granted to the app "
                                 "running Python (System Settings > Privacy & Security > Input Monitoring)")
        self._source = q.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        self._loop = run_loop
        q.CFRunLoopAddSource(run_loop, self._source, q.kCFRunLoopDefaultMode)
        q.CGEventTapEnable(self._tap, True)

    def close(self) -> None:
        q = self.q
        if self._tap is not None:
            q.CGEventTapEnable(self._tap, False)
        if self._loop is not None and self._source is not None:
            q.CFRunLoopRemoveSource(self._loop, self._source, q.kCFRunLoopDefaultMode)
        if self._tap is not None:
            q.CFMachPortInvalidate(self._tap)
        self._tap = self._source = self._loop = None

    # ---------------------------------------------------------- callback --
    def _on_event(self, proxy, event_type, event, refcon):
        q = self.q
        if event_type in (q.kCGEventTapDisabledByTimeout, q.kCGEventTapDisabledByUserInput):
            self.disabled_count += 1
            if self._tap is not None:
                q.CGEventTapEnable(self._tap, True)
            self.callback({"src": "session", "event": "tap_reenabled", "t": self.clock()})
            return event
        try:
            rec = self.normalize(event_type, event)
        except Exception as exc:      # never let a bad event kill the tap
            rec = {"src": "session", "event": "tap_error", "error": str(exc)[:200]}
        if rec is not None:
            rec["t"] = self.clock()
            self.callback(rec)
        return event

    def normalize(self, event_type: int, event: Any) -> Optional[dict]:
        q = self.q
        injected = self.is_injected(event)
        if event_type in (q.kCGEventLeftMouseDown, q.kCGEventRightMouseDown):
            p = q.CGEventGetLocation(event)
            return {"src": "mouse", "button": "left" if event_type == q.kCGEventLeftMouseDown else "right",
                    "pressed": True, "x": float(p.x), "y": float(p.y), "injected": injected,
                    "clicks": int(q.CGEventGetIntegerValueField(event, q.kCGMouseEventClickState))}
        if event_type == q.kCGEventKeyDown:
            code = int(q.CGEventGetIntegerValueField(event, q.kCGKeyboardEventKeycode))
            name = KEY_NAMES.get(code)
            if name not in REPORTED_KEYS:
                return None             # text-producing or uninteresting key: dropped, never decoded
            return {"src": "key", "key": name, "pressed": True, "injected": injected}
        return None

    def is_injected(self, event: Any) -> bool:
        """openadapt-capture darwin._is_injected: a positive source PID means another process posted it."""
        field = getattr(self.q, "kCGEventSourceUnixProcessID", None)
        if field is None:
            return False
        return int(self.q.CGEventGetIntegerValueField(event, field)) > 0
