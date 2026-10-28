"""
fake_backend.py — an in-memory desktop backend for tests (no GUI, no OS calls).

It implements the Backend protocol of ui_session.py:

    frontmost()            -> {"bundle_id", "pid", "window_id", "name", "pane"}
    snapshot()             -> the AX tree (a deep copy of ``self.tree``)
    perform(op, stop=None) -> records the op; type / set_value / paste ops
                              change the value of the editable node under the
                              op's centre, so a later snapshot shows the text
    user_took_over()       -> False, True, or "stop" (set ``took_over``);
                              one-shot, like a real "input since last poll" flag
    register_stop_hotkey(cb)  the session hands its stop callback; the test
                              presses it with ``press_stop_hotkey()``

``on_send(fake, op)`` runs when a click op carries a payload (a commit), so a
test can model the app clearing its compose window after sending.
"""
from __future__ import annotations

import copy
from typing import Any, Callable, List, Optional

_EDITABLE_ROLES = {"AXTextArea", "AXTextField", "AXComboBox", "AXSearchField",
                   "AXSecureTextField", "textarea", "input", "textfield"}

MAIL_ID = {"bundle_id": "com.apple.mail", "pid": 100, "window_id": 1, "name": "Mail"}


class FakeBackend:
    def __init__(self, tree: Any, identity: Optional[dict] = None, *, takeover_hook: bool = True):
        self.tree = tree
        self.identity = dict(MAIL_ID if identity is None else identity) if identity != {} else None
        self.ops: List[dict] = []
        self.took_over: Any = False
        self.hotkey_cb: Optional[Callable[[dict], bool]] = None
        self.on_send: Optional[Callable[["FakeBackend", dict], None]] = None
        self.on_snapshot: Optional[Callable[["FakeBackend"], None]] = None
        self.fail_with: Optional[BaseException] = None
        self.stop_seen: Any = None
        self.snapshots = 0
        if not takeover_hook:
            self.user_took_over = None          # type: ignore[assignment]

    # -- protocol ---------------------------------------------------------------
    def frontmost(self) -> Optional[dict]:
        return dict(self.identity) if self.identity else None

    def snapshot(self) -> Any:
        self.snapshots += 1
        if self.on_snapshot is not None:
            self.on_snapshot(self)
        return copy.deepcopy(self.tree)

    def perform(self, op: dict, stop: Any = None) -> Any:
        self.ops.append(op)
        self.stop_seen = stop
        if self.fail_with is not None:
            raise self.fail_with
        kind = op.get("op")
        if kind in ("type", "set_value", "paste") and op.get("center") and "text" in op:
            n = self._node_at(op["center"])
            if n is not None:
                n["value"] = op["text"] if kind == "set_value" else (n.get("value") or "") + op["text"]
        if op.get("payload") is not None and self.on_send is not None:
            self.on_send(self, op)
        return "done"

    def user_took_over(self) -> Any:            # replaced by None when takeover_hook=False
        t, self.took_over = self.took_over, False
        return t

    def register_stop_hotkey(self, cb: Callable[[dict], bool]) -> None:
        self.hotkey_cb = cb

    # -- test helpers -----------------------------------------------------------
    def press_stop_hotkey(self, synthetic: bool = False) -> bool:
        assert self.hotkey_cb is not None, "session did not register a stop hotkey"
        return self.hotkey_cb({"source": "synthetic" if synthetic else "hid",
                               "synthetic": synthetic, "pid": 4242 if synthetic else 0})

    def _node_at(self, center) -> Optional[dict]:
        x, y = center
        best = None

        def walk(n):
            nonlocal best
            if not isinstance(n, dict):
                return
            f = n.get("frame")
            if (n.get("role") in _EDITABLE_ROLES and isinstance(f, dict)
                    and f["x"] <= x <= f["x"] + f["w"] and f["y"] <= y <= f["y"] + f["h"]):
                best = n
            for c in n.get("children") or []:
                walk(c)
        walk(self.tree)
        return best
