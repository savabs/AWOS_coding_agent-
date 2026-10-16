"""
ui_session.py — a guarded desktop UI session: observe, act, send.

D16 in docs/research/rea_synthesis_2026-10-16.md. It ties together:
  * ax_observe.Observer   stable IDs, diff / full / no_change observations
  * TurnLease             capability scoped to a turn: act only on state read
                          in this turn, after the last action and after the
                          last user takeover
  * policy.classify_ui_action + Enforcer
                          every action is checked by the sink policy; a
                          destructive button is DESTRUCTIVE_UI (always asks
                          the Enforcer's approver, never auto-allowed)
  * sends.SendBroker      a send-like control is never clicked by act(); it
                          goes prepare_send -> approve_send -> commit_send

The backend is the only thing that touches the machine, so the whole module
runs on fakes in tests:

    class Backend(Protocol):
        def snapshot(self) -> dict: ...              # the AX tree, as JSON
        def perform(self, op: dict) -> Any: ...      # {"op": "click", "sid", "center", ...}
        # optional: def user_took_over(self) -> bool  (polled before each action)

For a commit the op is ``{"op": "click", ..., "payload": <approved payload>}``:
a real backend writes the (possibly owner-edited) payload into the compose
fields before pressing the control.

The lease (ChatGPT "Work with Apps": state must be read first and re-read
after the user takes over): an action is allowed only when the newest
observation is newer than the turn start, the last action, and the last
user takeover. A blocked action changes nothing, so it does not cost the
lease; a performed one does (its effect is unseen until the next observe).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence, Tuple

from .ax_observe import Observation, Observer
from .enforce import Enforcement, Enforcer
from .policy import Action, ActionClass, Provenance, classify_ui_action, is_send_control
from .sends import PreparedSend, SendApprover, SendBroker, SendRefused


class TurnLease:
    """Monotonic event clock. ``check()`` is None when acting is allowed,
    else the reason it is not."""

    def __init__(self) -> None:
        self._clock = 0
        self.turn_start = 0
        self.last_read: Optional[int] = None
        self.last_action = 0
        self.last_takeover = 0

    def _tick(self) -> int:
        self._clock += 1
        return self._clock

    def begin_turn(self) -> None:
        self.turn_start = self._tick()

    def observed(self) -> None:
        self.last_read = self._tick()

    def acted(self) -> None:
        self.last_action = self._tick()

    def user_takeover(self) -> None:
        self.last_takeover = self._tick()

    def check(self) -> Optional[str]:
        r = self.last_read
        if r is None or r < self.turn_start:
            return "no observation in this turn: observe before acting"
        if r < self.last_takeover:
            return "the user took over since the last observation: re-read before acting"
        if r < self.last_action:
            return "the last action changed the screen: observe before acting again"
        return None

    @property
    def valid(self) -> bool:
        return self.check() is None


@dataclass
class ActResult:
    ok: bool
    message: str
    action_class: Optional[ActionClass] = None
    enforcement: Optional[Enforcement] = None
    result: Any = None
    send: Optional[PreparedSend] = None
    by: str = ""                 # "" | lease | stale | policy | send-path | broker | backend


class DesktopSession:
    def __init__(self, backend: Any, enforcer: Enforcer, *,
                 send_approver: Optional[SendApprover] = None,
                 broker: Optional[SendBroker] = None,
                 viewport: Optional[Tuple[float, float, float, float]] = None,
                 max_elements: int = 300):
        self.backend = backend
        self.enforcer = enforcer
        self.observer = Observer(viewport, max_elements)
        self.lease = TurnLease()
        self.broker = broker or SendBroker(enforcer.policy, send_approver, enforcer.audit)
        self._send_sid: dict = {}

    # -- state ----------------------------------------------------------------
    @property
    def latest(self) -> Optional[Observation]:
        return self.observer.latest

    def begin_turn(self) -> None:
        self.lease.begin_turn()

    def user_takeover(self) -> None:
        """Call when the user has used the mouse/keyboard (or a host hook
        reports it): the agent must re-read the screen before acting."""
        self.lease.user_takeover()

    def observe(self) -> Observation:
        self._poll_takeover()
        obs = self.observer.observe(self.backend.snapshot())
        self.lease.observed()
        return obs

    def _poll_takeover(self) -> None:
        hook = getattr(self.backend, "user_took_over", None)
        if callable(hook):
            try:
                took = bool(hook())
            except Exception:                    # a broken hook counts as a takeover
                took = True
            if took:
                self.user_takeover()

    def _gate(self, sid: Optional[str]) -> Tuple[Optional[ActResult], Any]:
        self._poll_takeover()
        why = self.lease.check()
        if why:
            return ActResult(False, f"BLOCKED (lease): {why}", by="lease"), None
        el = None
        if sid is not None:
            el = self.latest.element(sid) if self.latest else None
            if el is None:
                return ActResult(False, f"BLOCKED: element {sid!r} is not in the latest "
                                        "observation (stale id): observe again", by="stale"), None
        return None, el

    # -- actions --------------------------------------------------------------
    def act(self, kind: str, sid: Optional[str] = None, *, text: Optional[str] = None,
            key: str = "", initiator: Provenance = Provenance.AGENT,
            arg_sources: Sequence[Provenance] = (Provenance.AGENT,)) -> ActResult:
        blocked, el = self._gate(sid)
        if blocked:
            return blocked
        obs = self.latest
        cls, target, why = classify_ui_action(
            kind, el, app=obs.app, key=key, text=text or "", focused=obs.focused,
            default_button=obs.default_button,
            send_visible=any(is_send_control(e) for e in obs.elements))
        if cls is ActionClass.SEND:
            return ActResult(False, f"BLOCKED: {why} sends outside the machine; use "
                                    "prepare_send -> approve_send -> commit_send",
                             cls, by="send-path")
        desc = f"{kind} {sid or ''} {why}".strip()
        res = self.enforcer.check_action(Action(cls, target, initiator, list(arg_sources), desc))
        if not res.allowed:
            return ActResult(False, f"BLOCKED: {res.message}", cls, res, by="policy")
        try:
            out = self.backend.perform(self._op(kind, sid, el, text, key))
        finally:
            self.lease.acted()       # even a scroll or a failed op may have changed the screen
        return ActResult(True, f"ok ({why}); observe before the next action", cls, res, out)

    @staticmethod
    def _op(kind: str, sid, el, text, key) -> dict:
        op = {"op": kind, "sid": sid}
        if el is not None:
            op["center"] = el.center
        if text is not None:
            op["text"] = text
        if key:
            op["key"] = key
        return op

    # -- two-phase sends ------------------------------------------------------
    def prepare_send(self, sid: str, payload: Any, *, kind: str = "ui_send",
                     recipient: str = "", key: Optional[str] = None,
                     initiator: Provenance = Provenance.AGENT,
                     arg_sources: Sequence[Provenance] = (Provenance.AGENT,)) -> ActResult:
        blocked, el = self._gate(sid)
        if blocked:
            return blocked
        cls, target, why = classify_ui_action("click", el, app=self.latest.app)
        if cls is not ActionClass.SEND:
            return ActResult(False, f"BLOCKED: {sid} is not a send control ({why}); use act()",
                             cls, by="send-path")
        if recipient:
            target = f"{target}->{recipient}"
        try:
            p = self.broker.prepare(kind, target, payload, initiator=initiator,
                                    arg_sources=arg_sources, key=key,
                                    description=f"{kind} via {sid}: {why}")
        except SendRefused as e:
            return ActResult(False, f"BLOCKED: {e}", cls, by="broker")
        self._send_sid[p.id] = (sid, el.label or el.text, el.role)
        return ActResult(True, f"prepared send {p.id}; awaiting owner approval", cls, send=p)

    def approve_send(self, send_id: str, approver: Optional[SendApprover] = None) -> ActResult:
        try:
            p = self.broker.approve(send_id, approver)
        except SendRefused as e:
            return ActResult(False, f"BLOCKED: {e}", ActionClass.SEND, by="broker")
        ok = p.status == "approved"
        return ActResult(ok, f"send {send_id} {p.status}" + (" (edited)" if p.edited else ""),
                         ActionClass.SEND, send=p, by="" if ok else "broker")

    def commit_send(self, send_id: str) -> ActResult:
        if send_id not in self._send_sid:
            return ActResult(False, f"BLOCKED: unknown send {send_id!r}", ActionClass.SEND,
                             by="broker")
        sid, label, role = self._send_sid[send_id]
        blocked, el = self._gate(sid)
        if blocked:
            return blocked
        if (el.label or el.text, el.role) != (label, role):
            return ActResult(False, f"BLOCKED: element {sid} changed since prepare",
                             ActionClass.SEND, by="stale")
        p = self.broker.sends[send_id]

        def execute(payload):
            return self.backend.perform({"op": "click", "sid": sid, "center": el.center,
                                         "payload": payload, "idempotency_key": p.key})
        try:
            out = self.broker.commit(send_id, execute)
        except SendRefused as e:
            return ActResult(False, f"BLOCKED: {e}", ActionClass.SEND, send=p, by="broker")
        except Exception as e:       # the backend failed mid-send: it may have sent
            return ActResult(False, f"FAILED ({type(e).__name__}: {e}); the key is burned, "
                                    "so this send is never retried automatically",
                             ActionClass.SEND, send=p, by="backend")
        finally:
            if p.status in ("committed", "failed"):
                self.lease.acted()
        return ActResult(True, f"sent ({send_id}); observe before the next action",
                         ActionClass.SEND, result=out, send=p)
