"""
stop.py — a latched emergency stop for desktop computer use.

    stop = StopSwitch(sentinel=".awos/cu_stop", audit=AuditLog(path))
    stop.check()          # None, or the reason the session is stopped

Triggers (any one latches the switch):
  * a physical hotkey: the backend registers a global key hook and calls
    ``stop.on_hotkey(event)``. The event must come from hardware: a backend
    reports ``{"source": "hid", "synthetic": false, "pid": 0}`` for a real key
    press; anything synthetic (posted by a process, e.g. the agent's own
    key events, or with a non-zero poster pid) is rejected, so the agent
    cannot fake the stop *or* learn to send it as a no-op;
  * a sentinel file (``.awos/cu_stop`` by default, ``AWOS_CU_STOP_FILE`` or
    ``sentinel=``): its existence trips the switch at the next poll;
  * a programmatic call ``stop.trip(reason)`` or a signal handler installed
    with ``stop.install_signal()`` (SIGUSR1 by default).

The switch is polled by DesktopSession in observe, before every action and
inside commit_send, and is passed to multi-step backend operations as a
``stop`` callable (true when stopped). Listeners registered with
``on_trip`` run once when it trips (the session uses this to kill its lease
and reject every prepared or approved send). Each trip writes an audit
record ``{"kind": "cu_stop", "source", "reason", ...}``.

Latched: there is no method on the switch to clear it. Only
``owner_reset(switch)`` does that, a module function the agent's tool
surface never exposes; it refuses while the sentinel file still exists,
and it is audited too.
"""
from __future__ import annotations

import os
import signal
import threading
import time
from pathlib import Path
from typing import Any, Callable, List, Optional, Union

from .policy import AuditLog

STOP_FILE_ENV = "AWOS_CU_STOP_FILE"
DEFAULT_STOP_FILE = Path(".awos") / "cu_stop"


def _is_hardware(event: Any) -> bool:
    """A real key press: source "hid", not synthetic, no poster pid."""
    get = (event.get if isinstance(event, dict)
           else lambda k, d=None: getattr(event, k, d))
    if get("synthetic", True) is not False:
        return False
    if str(get("source", "")).lower() != "hid":
        return False
    return get("pid", 0) in (0, None)


class StopSwitch:
    def __init__(self, sentinel: Union[str, Path, None] = None,
                 audit: Optional[AuditLog] = None):
        raw = sentinel if sentinel is not None else (
            os.getenv(STOP_FILE_ENV, "").strip() or DEFAULT_STOP_FILE)
        self.sentinel: Optional[Path] = Path(raw) if raw else None
        self.audit = audit
        self._lock = threading.Lock()
        self._reason: Optional[str] = None
        self._source = ""
        self._listeners: List[Callable[[str], None]] = []
        self.rejected_hotkeys = 0

    # -- state ------------------------------------------------------------------
    @property
    def tripped(self) -> bool:
        return self.check() is not None

    @property
    def reason(self) -> Optional[str]:
        return self._reason

    def check(self) -> Optional[str]:
        """Poll the sentinel; the stop reason, or None when not stopped."""
        if self._reason is None and self.sentinel is not None:
            try:
                present = self.sentinel.exists()
            except OSError:
                present = True              # cannot tell: fail closed
            if present:
                self.trip(f"sentinel file {self.sentinel} present", source="sentinel")
        return self._reason

    def __call__(self) -> bool:
        """For backends: ``stop()`` is True once stopped."""
        return self.tripped

    # -- triggers ---------------------------------------------------------------
    def trip(self, reason: str = "stopped", source: str = "programmatic") -> None:
        with self._lock:
            if self._reason is not None:
                return
            self._reason, self._source = f"{reason} ({source})", source
            listeners = list(self._listeners)
        if self.audit is not None:
            self.audit.append_record({"ts": round(time.time(), 3), "kind": "cu_stop",
                                      "source": source, "reason": reason})
        for fn in listeners:
            try:
                fn(self._reason)
            except Exception:               # a listener must never un-stop anything
                pass

    def on_hotkey(self, event: Any) -> bool:
        """Backend hook for the physical stop hotkey. True when it tripped."""
        if not _is_hardware(event):
            self.rejected_hotkeys += 1
            if self.audit is not None:
                self.audit.append_record({"ts": round(time.time(), 3), "kind": "cu_stop",
                                          "source": "hotkey", "outcome": "rejected-synthetic"})
            return False
        self.trip("stop hotkey pressed", source="hotkey")
        return True

    def install_signal(self, signum: int = signal.SIGUSR1):
        """Trip on ``signum``. Returns the previous handler."""
        return signal.signal(signum, lambda *_: self.trip(f"signal {signum}", source="signal"))

    def on_trip(self, fn: Callable[[str], None]) -> None:
        """Run ``fn(reason)`` when the switch trips (at once if it already has)."""
        with self._lock:
            self._listeners.append(fn)
            reason = self._reason
        if reason is not None:
            fn(reason)


def owner_reset(switch: StopSwitch, *, note: str = "") -> None:
    """Owner-side reset. Not part of any agent tool; refuses while the
    sentinel file still exists (remove it first)."""
    if switch.sentinel is not None and switch.sentinel.exists():
        raise PermissionError(f"remove {switch.sentinel} before resetting the stop switch")
    with switch._lock:
        was, switch._reason, switch._source = switch._reason, None, ""
    if switch.audit is not None:
        switch.audit.append_record({"ts": round(time.time(), 3), "kind": "cu_stop",
                                    "source": "owner_reset", "was": was, "note": note})
