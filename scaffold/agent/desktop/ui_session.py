"""
ui_session.py — a guarded desktop UI session: observe, act, send.

D16 in docs/research/rea_synthesis_2026-10-16.md. It ties together:
  * ax_observe.Observer   stable IDs, diff / full / no_change observations,
                          ambiguous IDs after a group changes size
  * app_policy.AppPolicy  which app may be observed / clicked / typed into,
                          keyed by the bundle id the backend reports
  * stop.StopSwitch       latched emergency stop (hotkey, sentinel, signal)
  * TurnLease             capability scoped to a turn: act only on state read
                          in this turn, after the last action and after the
                          last user takeover, and not while yielded to the user
  * policy.classify_ui_action + Enforcer
                          every action is checked by the sink policy; a
                          destructive button is DESTRUCTIVE_UI (always asks
                          the Enforcer's approver, never auto-allowed)
  * sends.SendBroker      a send-like control is never clicked by act(); it
                          goes prepare_send -> approve_send -> commit_send

The backend is the only thing that touches the machine, so the whole module
runs on fakes in tests (fake_backend.FakeBackend):

    class Backend(Protocol):
        def frontmost(self) -> dict: ...
            # {"bundle_id", "pid", "window_id", "name", "pane"} of the app
            # that owns the focused window, from the OS process table (e.g.
            # NSWorkspace.frontmostApplication + the AX window id), NEVER
            # from a window title
        def snapshot(self) -> dict: ...              # that app's AX tree, as JSON
        def perform(self, op: dict, stop=None) -> Any: ...
            # {"op": "click", "sid", "center", ...}; a multi-step op must poll
            # ``stop()`` between steps and abort when it is true
        # optional: def user_took_over(self) -> bool | "stop"
        #     polled before each observation and action; "stop" means the
        #     user asked to stop (escalates to the stop switch)
        # optional: def register_stop_hotkey(self, cb) -> None
        #     global key hook; cb(event) with event {"source": "hid" |
        #     "synthetic", "synthetic": bool, "pid": poster pid}; only a
        #     hardware event (hid, not synthetic, pid 0) trips the stop

For a commit the op is ``{"op": "click", ..., "payload": <approved payload>,
"idempotency_key": ...}``. Before that click the session itself writes the
approved payload into the recorded compose fields (``set_value``) when they
differ, re-reads the screen and refuses unless the fields now hold exactly
the approved text.

Every result has a typed ``outcome`` (Outcome): ok, needs_observation,
stale, ambiguous, pending_approval, not_allowed, forbidden_target, stopped,
backend_error_maybe_applied; ``by`` names the layer that decided (lease,
stop, app, stale, ambiguous, policy, send-path, provenance, broker, backend).

Order of checks for act():
  1. stop switch; takeover hook (a takeover moves the lease to "yielded";
     "stop" trips the stop switch); a backend without a takeover hook is
     refused unless the owner declared the session unattended (a VM)
  2. lease (observed this turn, after the last action and takeover, quiet
     period over); app tier of the observed app vs the action kind
  3. the element id: present (else stale), not ambiguous (else ambiguous)
  4. classification (policy.classify_ui_action) -> sends go to the
     two-phase path; typing into a secure field needs owner-provided text;
     input into a terminal is opaque and its command must not be denied
  5. provenance: typed text is checked by policy.provenance_of against the
     owner's texts and the screen; the stricter of computed and declared
     wins; a recipient / URL / amount the owner never wrote makes it ask
  6. the Enforcer (elevated apps: every action asks)
  7. action time: stop again, takeover again, lease again; after an approval
     wait a fresh snapshot, and the target id must resolve to the same
     role / label / ancestors / row; the backend's frontmost app and window
     must still be the observed one
  8. perform (the stop switch is passed along); an exception is
     backend_error_maybe_applied, and the lease is spent either way
"""
from __future__ import annotations

import inspect
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .app_policy import AppIdentity, AppPolicy, AppRule, AppTier
from .ax_compress import EDITABLE
from .ax_observe import Observation, Observer, identity_key
from .enforce import Enforcement, Enforcer
from .policy import (
    UI_INPUT_KINDS, Action, ActionClass, Provenance, Verdict, classify_ui_action,
    decide_command, is_send_control, least_trusted, provenance_of, unquotable_entities,
)
from .sends import PreparedSend, SendApprover, SendBroker, SendRefused
from .stop import StopSwitch

DEFAULT_QUIET_PERIOD = 3.0          # seconds without user input before acting again
_TYPING = {"type", "paste", "set_value"}


class Outcome(str, Enum):
    OK = "ok"
    NEEDS_OBSERVATION = "needs_observation"
    STALE = "stale"
    AMBIGUOUS = "ambiguous"
    PENDING_APPROVAL = "pending_approval"
    NOT_ALLOWED = "not_allowed"
    FORBIDDEN_TARGET = "forbidden_target"
    STOPPED = "stopped"
    BACKEND_ERROR_MAYBE_APPLIED = "backend_error_maybe_applied"


class TurnLease:
    """Monotonic event clock. ``check()`` is None when acting is allowed,
    else the reason it is not.

    States: active; yielded (the user took over less than ``quiet_period``
    seconds ago); ended (end_turn() until begin_turn()); stopped (kill(),
    permanent)."""

    def __init__(self, quiet_period: float = DEFAULT_QUIET_PERIOD,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = 0
        self.turn_start = 0
        self.last_read: Optional[int] = None
        self.last_action = 0
        self.last_takeover = 0
        self.turn_open = True
        self.quiet_period = quiet_period
        self.clock = clock
        self.yielded_at: Optional[float] = None
        self.dead: Optional[str] = None

    def _tick(self) -> int:
        self._clock += 1
        return self._clock

    def begin_turn(self) -> None:
        self.turn_open = True
        self.turn_start = self._tick()

    def end_turn(self) -> None:
        self.turn_open = False
        self._tick()

    def observed(self) -> None:
        self.last_read = self._tick()

    def acted(self) -> None:
        self.last_action = self._tick()

    invalidate = acted        # the screen changed in a way the model has not seen

    def user_takeover(self) -> None:
        self.last_takeover = self._tick()
        self.yielded_at = self.clock()

    def kill(self, reason: str) -> None:
        self.dead = reason

    def _quiet_left(self) -> float:
        if self.yielded_at is None:
            return 0.0
        return max(0.0, self.quiet_period - (self.clock() - self.yielded_at))

    @property
    def state(self) -> str:
        if self.dead:
            return "stopped"
        if not self.turn_open:
            return "ended"
        return "yielded" if self._quiet_left() > 0 else "active"

    def check(self) -> Optional[str]:
        if self.dead:
            return f"stopped: {self.dead}"
        if not self.turn_open:
            return "the turn has ended: begin a new turn first"
        r = self.last_read
        if r is None or r < self.turn_start:
            return "no observation in this turn: observe before acting"
        if r < self.last_takeover:
            return "the user took over since the last observation: re-read before acting"
        left = self._quiet_left()
        if left > 0:
            return (f"yielded to the user: waiting for {self.quiet_period:.0f}s without user "
                    f"input ({left:.1f}s left)")
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
    by: str = ""                 # layer: "" | lease | stop | app | stale | ambiguous | policy |
    #                              send-path | provenance | broker | backend
    outcome: Outcome = Outcome.OK
    warning: str = ""


def _fail(outcome: Outcome, by: str, message: str, cls: Optional[ActionClass] = None,
          **kw) -> ActResult:
    return ActResult(False, f"BLOCKED ({outcome.value}): {message}", cls, by=by,
                     outcome=outcome, **kw)


def _lease_fail(lease: "TurnLease", why: str) -> ActResult:
    return _fail(Outcome.STOPPED if lease.dead else Outcome.NEEDS_OBSERVATION, "lease", why)


def _norm(s: Any) -> str:
    return " ".join(str(s or "").split())


class DesktopSession:
    def __init__(self, backend: Any, enforcer: Enforcer, *,
                 send_approver: Optional[SendApprover] = None,
                 broker: Optional[SendBroker] = None,
                 viewport: Optional[Tuple[float, float, float, float]] = None,
                 max_elements: int = 300,
                 app_policy: Optional[AppPolicy] = None,
                 stop: Optional[StopSwitch] = None,
                 owner_texts: Sequence[str] = (),
                 unattended: bool = False,
                 quiet_period: float = DEFAULT_QUIET_PERIOD,
                 clock: Callable[[], float] = time.monotonic,
                 diff: Optional[bool] = None,
                 full_every: Optional[int] = None):
        self.backend = backend
        self.enforcer = enforcer
        self.observer = Observer(viewport, max_elements, diff=diff, full_every=full_every)
        self.lease = TurnLease(quiet_period, clock)
        self.broker = broker or SendBroker(enforcer.policy, send_approver, enforcer.audit)
        self.app_policy = app_policy if app_policy is not None else AppPolicy.load()
        self.unattended = bool(unattended or self.app_policy.unattended)
        self.stop = stop if stop is not None else StopSwitch(audit=enforcer.audit)
        self.owner_texts: List[str] = [t for t in owner_texts if t]
        self._agent_texts: List[str] = []
        self._screen: Dict[str, bool] = {}
        self._rule: Optional[AppRule] = None
        self._refusal: Optional[Outcome] = None
        self._send_sid: dict = {}
        self.stop.on_trip(self._on_stop)
        reg = getattr(backend, "register_stop_hotkey", None)
        if callable(reg):
            reg(self.stop.on_hotkey)

    # -- state ----------------------------------------------------------------
    @property
    def latest(self) -> Optional[Observation]:
        return self.observer.latest

    @property
    def state(self) -> str:
        return self.lease.state

    def begin_turn(self) -> None:
        self.lease.begin_turn()

    def end_turn(self) -> None:
        self.lease.end_turn()

    def user_takeover(self) -> None:
        """Call when the user has used the mouse/keyboard (or a host hook
        reports it): the session yields; it must re-read the screen, and wait
        for the quiet period, before acting."""
        self.lease.user_takeover()

    def add_owner_text(self, text: str) -> None:
        """Text the owner wrote or approved (the goal, an edited payload)."""
        if text:
            self.owner_texts.append(str(text))

    def _on_stop(self, reason: str) -> None:
        self.lease.kill(reason)
        rejected = self.broker.halt(reason)
        if self.enforcer.audit is not None:
            self.enforcer.audit.append_record({"ts": round(time.time(), 3), "kind": "cu_stop",
                                               "source": "session", "reason": reason,
                                               "rejected_sends": rejected})

    # -- backend helpers ------------------------------------------------------
    def _frontmost(self) -> Optional[AppIdentity]:
        fn = getattr(self.backend, "frontmost", None)
        if not callable(fn):
            return None
        try:
            return AppIdentity.from_any(fn())
        except Exception:
            return None

    @property
    def _has_takeover_hook(self) -> bool:
        return callable(getattr(self.backend, "user_took_over", None))

    def _poll_takeover(self) -> None:
        hook = getattr(self.backend, "user_took_over", None)
        if not callable(hook):
            return
        try:
            took = hook()
        except Exception:                    # a broken hook counts as a takeover
            took = True
        if isinstance(took, str) and took.strip().lower() in ("stop", "stopped",
                                                               "stopped_by_user"):
            self.stop.trip("stopped by user", source="takeover")
        elif took:
            self.user_takeover()

    def _perform(self, op: dict) -> Any:
        fn = self.backend.perform
        try:
            params = inspect.signature(fn).parameters.values()
            takes_stop = any(p.name == "stop" or p.kind is p.VAR_KEYWORD for p in params)
        except (TypeError, ValueError):
            takes_stop = False
        return fn(op, stop=self.stop) if takes_stop else fn(op)

    # -- observation ----------------------------------------------------------
    def _refused(self, kind: str, message: str, ident: Optional[AppIdentity],
                 outcome: Outcome) -> Observation:
        self.observer.seq += 1
        obs = Observation(self.observer.seq, kind, f"obs {self.observer.seq}: {kind}: {message}",
                          [], [], getattr(ident, "name", ""), "", identity=ident,
                          app_key=("refused", self.observer.seq))
        self.observer.latest = obs
        self._refusal, self._rule = outcome, None
        return obs

    def observe(self, full: bool = False) -> Observation:
        """Read the frontmost app. ``full=True`` forces a full tree (and
        clears ambiguous IDs flagged before this observation)."""
        why = self.stop.check()
        if why is None:
            self._poll_takeover()
            why = self.stop.check()
        if why is not None:
            return self._refused("stopped", f"computer use is stopped: {why}", None, Outcome.STOPPED)
        ident = self._frontmost()
        rule = self.app_policy.rule(ident)
        if rule.tier is AppTier.FORBIDDEN:
            who = ident.bundle_id if ident else "an app the backend did not identify"
            return self._refused("refused", f"not observing {who}: {rule.reason}", ident,
                                 Outcome.FORBIDDEN_TARGET)
        tree = self.backend.snapshot()
        after = self._frontmost()
        tid = tree if isinstance(tree, dict) else {}
        if (not ident.same_window(after)
                or (tid.get("bundle_id") not in (None, "", ident.bundle_id))
                or (tid.get("pid") not in (None, ident.pid))):
            return self._refused("refused", "the frontmost app or window changed during the "
                                 "snapshot: observe again", ident, Outcome.STALE)
        warning = ""
        if rule.elevated:
            warning = (f"{ident.name or ident.bundle_id} is an elevated-risk app ({rule.reason}): "
                       "field values are hidden and every action asks the owner")
        obs = self.observer.observe(tree, full=full, identity=ident,
                                    redact_values=rule.elevated, warning=warning)
        self._rule, self._refusal = rule, None
        for e in obs.elements:               # text -> True when it is an editable field's value
            for t in (e.text, e.selected_text):
                if t:
                    self._screen[t] = False
            if e.value:
                self._screen[e.value] = self._screen.get(e.value, True) and e.role in EDITABLE
        while len(self._screen) > 5000:
            self._screen.pop(next(iter(self._screen)))
        self.lease.observed()
        return obs

    # -- gates ----------------------------------------------------------------
    def _gate(self, sid: Optional[str], kind: str) -> Tuple[Optional[ActResult], Any]:
        why = self.stop.check()
        if why is None:
            self._poll_takeover()
            why = self.stop.check()
        if why is not None:
            return _fail(Outcome.STOPPED, "stop", f"computer use is stopped: {why}"), None
        obs = self.latest
        if obs is not None and self._refusal is not None:
            return _fail(self._refusal, "app" if self._refusal is Outcome.FORBIDDEN_TARGET
                         else "stale", obs.text), None
        if not self._has_takeover_hook and not self.unattended:
            return _fail(Outcome.NOT_ALLOWED, "lease",
                         "the backend cannot report user takeover; acting needs a takeover "
                         "hook, or the owner declaring this session unattended (e.g. a VM)"), None
        why = self.lease.check()
        if why:
            return _lease_fail(self.lease, why), None
        rule = self._rule
        if rule is None or not rule.allows_kind(kind):
            tier = rule.tier.value if rule else "unknown"
            app = getattr(obs.identity, "bundle_id", "?")
            return _fail(Outcome.FORBIDDEN_TARGET, "app",
                         f"{kind!r} is not allowed in {app} (app tier {tier}"
                         + (f": {rule.reason}" if rule and rule.reason else "") + ")"), None
        el = None
        if sid is not None:
            el = obs.element(sid)
            if el is None:
                return _fail(Outcome.STALE, "stale", f"element {sid!r} is not in the latest "
                             "observation (stale id): observe again"), None
            if self.observer.is_ambiguous(sid):
                return _fail(Outcome.AMBIGUOUS, "ambiguous",
                             f"element {sid!r} is ambiguous: elements like it were added or "
                             "removed, so its id may point at a different one; "
                             "observe(full=True) and pick again"), None
            if el.offscreen and kind not in ("scroll", "hover", "observe", "read", "wait"):
                return _fail(Outcome.STALE, "stale", f"element {sid!r} is scrolled out of "
                             "view: scroll it into view and observe again"), None
        return None, el

    def _action_time(self, sid: Optional[str], el: Any, approved: bool) -> Tuple[Optional[ActResult], Any]:
        """Right before perform: stop, takeover, lease, fresh read after an
        approval wait (target must re-resolve), frontmost app/window. The
        app/window must be the one of the observation the model acted on,
        not merely the one of the fresh read."""
        ident = self.latest.identity if self.latest else None
        why = self.stop.check()
        if why is not None:
            return _fail(Outcome.STOPPED, "stop", f"computer use is stopped: {why}"), None
        self._poll_takeover()
        if self.stop.check() is not None:
            return _fail(Outcome.STOPPED, "stop", f"computer use is stopped: {self.stop.reason}"), None
        if not approved:
            why = self.lease.check()
            if why:
                return _lease_fail(self.lease, why), None
        else:
            # a wait happened (owner approval, or the session's own fill step):
            # a takeover since the model's read still blocks; then re-read
            lease = self.lease
            if lease.dead or not lease.turn_open or lease.last_read is None \
                    or lease.last_read < lease.last_takeover:
                return _lease_fail(lease, lease.check() or
                                   "the user took over: re-read before acting"), None
            want = identity_key(el) if el is not None else None
            fresh = self.observe()
            if fresh.kind in ("refused", "stopped"):
                return _fail(self._refusal or Outcome.STALE, "stale", fresh.text), None
            if ident is None or not ident.same_window(fresh.identity):
                return _fail(Outcome.STALE, "stale", "after the wait a different app or window "
                             "is in front: observe again"), None
            why = self.lease.check()
            if why:
                return _lease_fail(self.lease, why), None
            if sid is not None:
                el2 = fresh.element(sid)
                if el2 is None or identity_key(el2) != want or self.observer.is_ambiguous(sid):
                    return _fail(Outcome.STALE, "stale", f"after the approval wait element {sid!r} "
                                 "no longer resolves to the same control: observe again"), None
                el = el2
        front = self._frontmost()
        if front is None or ident is None or not ident.same_window(front):
            got = f"{front.bundle_id} pid {front.pid} window {front.window_id}" if front else "unknown"
            return _fail(Outcome.STALE, "stale", f"the frontmost app/window is now {got}, not the "
                         "observed one: observe again"), None
        return None, el

    # -- provenance -----------------------------------------------------------
    def _screen_texts(self) -> List[str]:
        """Screen text seen this session, minus field values that are just
        what the agent itself typed there (its own words are not screen data)."""
        mine = [_norm(a).casefold() for a in self._agent_texts]
        return [t for t, field_value in self._screen.items()
                if not (field_value and any(_norm(t).casefold() in a for a in mine))]

    def _sources_for(self, values: Sequence[str], declared: Optional[Sequence[Provenance]]
                     ) -> List[Provenance]:
        out = [Provenance(s) for s in declared] if declared is not None else []
        screen = self._screen_texts()
        for v in values:
            if not v:
                continue
            out.append(provenance_of(v, self.owner_texts, screen))
            if unquotable_entities(v, self.owner_texts):
                out.append(Provenance.UNTRUSTED)   # recipient / URL / amount the owner never wrote
        return out or [Provenance.AGENT]

    # -- actions --------------------------------------------------------------
    def act(self, kind: str, sid: Optional[str] = None, *, text: Optional[str] = None,
            key: str = "", initiator: Provenance = Provenance.AGENT,
            arg_sources: Optional[Sequence[Provenance]] = None) -> ActResult:
        blocked, el = self._gate(sid, kind)
        if blocked:
            return blocked
        obs, rule = self.latest, self._rule
        app = obs.identity.bundle_id
        cls, target, why = classify_ui_action(
            kind, el, app=app, key=key, text=text or "", focused=obs.focused,
            default_button=obs.default_button, shell=rule.shell,
            send_visible=any(is_send_control(e) for e in obs.elements))
        if cls is ActionClass.SEND:
            return _fail(Outcome.NOT_ALLOWED, "send-path", f"{why} sends outside the machine; use "
                         "prepare_send -> approve_send -> commit_send", cls)
        sources = self._sources_for([text or ""], arg_sources)
        text_prov = least_trusted(sources)
        tgt_el = el if el is not None else obs.focused
        if kind in _TYPING and getattr(tgt_el, "secure", False) and text_prov is not Provenance.OWNER:
            return _fail(Outcome.NOT_ALLOWED, "provenance",
                         "typing into a secure / password / card field needs text the owner "
                         f"provided (this text is {text_prov.value})", cls)
        if rule.shell and kind in UI_INPUT_KINDS and text:
            d = decide_command(self.enforcer.policy, text.strip(), initiator, sources)
            if d.verdict is Verdict.DENY:
                return _fail(Outcome.NOT_ALLOWED, "policy", f"typed into a terminal this is the "
                             f"shell command {text.strip()!r}: {d.reason} ({d.rule})", cls)
        desc = f"{kind} {sid or ''} {why}".strip()
        res = self.enforcer.check_action(Action(cls, target, initiator, sources, desc),
                                         force_ask=f"elevated-risk app ({rule.reason})"
                                         if rule.elevated else "")
        if not res.allowed:
            return _fail(Outcome.NOT_ALLOWED, "policy", res.message, cls, enforcement=res)
        blocked, el = self._action_time(sid, el, approved=res.outcome == "approved")
        if blocked:
            blocked.action_class, blocked.enforcement = cls, res
            return blocked
        try:
            out = self._perform(self._op(kind, sid, el, text, key))
        except Exception as e:
            return ActResult(False, f"FAILED ({type(e).__name__}: {e}); the action may have "
                             "been applied: observe before doing anything else", cls, res,
                             by="backend", outcome=Outcome.BACKEND_ERROR_MAYBE_APPLIED)
        finally:
            self.lease.acted()       # even a scroll or a failed op may have changed the screen
        if text and kind in _TYPING:
            self._agent_texts.append(text)
        if self.stop.check() is not None:
            return ActResult(False, f"stopped during the action ({self.stop.reason}); it may "
                             "have been applied", cls, res, out, by="stop",
                             outcome=Outcome.STOPPED)
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
    def _resolve_fields(self, payload: Any, fields: Optional[Dict[Any, str]]
                        ) -> Tuple[Optional[Dict[Any, dict]], str]:
        """payload part -> {"sid", "key"} of the compose field holding it."""
        obs = self.latest
        parts = payload if isinstance(payload, dict) else {"": payload}
        out: Dict[Any, dict] = {}
        for k, v in parts.items():
            if not isinstance(v, str):
                return None, f"payload part {k!r} is not text"
            sid = (fields or {}).get(k)
            if sid is None and fields is None:
                cands = [e for e in obs.elements if e.role in EDITABLE and not e.secure
                         and _norm(e.raw_value) == _norm(v) and _norm(v)]
                if len(cands) == 1:
                    sid = cands[0].sid
            if sid is None:
                return None, (f"say which compose field holds payload part {k!r} "
                              "(fields={part: element id}), or type it there first")
            e = obs.element(sid)
            if e is None or e.role not in EDITABLE or e.secure:
                return None, f"field {sid!r} for payload part {k!r} is not a visible text field"
            out[k] = {"sid": sid, "key": identity_key(e)}
        return out, ""

    def prepare_send(self, sid: str, payload: Any, *, kind: str = "ui_send",
                     recipient: str = "", key: Optional[str] = None,
                     fields: Optional[Dict[Any, str]] = None,
                     initiator: Provenance = Provenance.AGENT,
                     arg_sources: Optional[Sequence[Provenance]] = None) -> ActResult:
        blocked, el = self._gate(sid, "click")
        if blocked:
            return blocked
        app = self.latest.identity.bundle_id
        cls, target, why = classify_ui_action("click", el, app=app)
        if cls is not ActionClass.SEND:
            return _fail(Outcome.NOT_ALLOWED, "send-path",
                         f"{sid} is not a send control ({why}); use act()", cls)
        recs, err = self._resolve_fields(payload, fields)
        if recs is None:
            return _fail(Outcome.NOT_ALLOWED, "send-path", err, cls)
        parts = payload if isinstance(payload, dict) else {"": payload}
        sources = self._sources_for([recipient, *[v for v in parts.values()]], arg_sources)
        if recipient:
            target = f"{target}->{recipient}"
        try:
            p = self.broker.prepare(kind, target, payload, initiator=initiator,
                                    arg_sources=sources, key=key,
                                    description=f"{kind} via {sid}: {why}")
        except SendRefused as e:
            return _fail(Outcome.NOT_ALLOWED, "broker", str(e), cls)
        p.fields = recs
        self._send_sid[p.id] = {"sid": sid, "key": identity_key(el), "recipient": recipient}
        return ActResult(True, f"prepared send {p.id}; awaiting owner approval", cls, send=p,
                         outcome=Outcome.PENDING_APPROVAL)

    def approve_send(self, send_id: str, approver: Optional[SendApprover] = None) -> ActResult:
        try:
            p = self.broker.approve(send_id, approver)
        except SendRefused as e:
            return _fail(Outcome.NOT_ALLOWED, "broker", str(e), ActionClass.SEND)
        ok = p.status == "approved"
        if ok:                                   # the owner has seen and approved this text
            parts = p.payload if isinstance(p.payload, dict) else {"": p.payload}
            for v in parts.values():
                if isinstance(v, str):
                    self.add_owner_text(v)
            self.add_owner_text(self._send_sid.get(send_id, {}).get("recipient", ""))
        return ActResult(ok, f"send {send_id} {p.status}" + (" (edited)" if p.edited else ""),
                         ActionClass.SEND, send=p, by="" if ok else "broker",
                         outcome=Outcome.OK if ok else Outcome.NOT_ALLOWED)

    def _fields_match(self, p: PreparedSend) -> Tuple[Optional[List[tuple]], str]:
        """[(field element, wanted text)] when every recorded field still
        resolves to the same control; else (None, why)."""
        obs = self.latest
        parts = p.payload if isinstance(p.payload, dict) else {"": p.payload}
        out = []
        for k, rec in p.fields.items():
            e = obs.element(rec["sid"])
            if e is None or identity_key(e) != rec["key"] or self.observer.is_ambiguous(rec["sid"]):
                return None, f"compose field {rec['sid']!r} for {k!r} changed or disappeared"
            out.append((e, parts.get(k, "") if isinstance(parts.get(k, ""), str) else ""))
        return out, ""

    def commit_send(self, send_id: str) -> ActResult:
        rec = self._send_sid.get(send_id)
        if rec is None:
            return _fail(Outcome.NOT_ALLOWED, "broker", f"unknown send {send_id!r}", ActionClass.SEND)
        sid = rec["sid"]
        blocked, el = self._gate(sid, "click")
        if blocked:
            return blocked
        try:
            p = self.broker.precommit_check(send_id)
        except SendRefused as e:
            return _fail(Outcome.NOT_ALLOWED, "broker", str(e), ActionClass.SEND,
                         send=self.broker.sends.get(send_id))
        if identity_key(el) != rec["key"]:
            return _fail(Outcome.STALE, "stale", f"element {sid} changed since prepare",
                         ActionClass.SEND, send=p)
        # an approval wait happened: fresh snapshot, same send control
        blocked, el = self._action_time(sid, el, approved=True)
        if blocked:
            blocked.action_class, blocked.send = ActionClass.SEND, p
            return blocked
        pairs, err = self._fields_match(p)
        if pairs is None:
            return _fail(Outcome.STALE, "stale", err, ActionClass.SEND, send=p)
        todo = [(e, want) for e, want in pairs if _norm(e.raw_value) != _norm(want)]
        rule = self._rule
        if todo and (rule is None or rule.shell or not rule.allows_kind("set_value")):
            return _fail(Outcome.FORBIDDEN_TARGET, "app", "the compose fields do not hold the "
                         "approved text and this app's tier does not allow writing it; nothing "
                         "was sent", ActionClass.SEND, send=p)
        if todo:                     # write the approved (maybe owner-edited) text, then re-read
            try:
                for e, want in todo:
                    self._perform({"op": "set_value", "sid": e.sid, "center": e.center,
                                   "text": want})
            except Exception as ex:
                self.lease.acted()
                return ActResult(False, f"FAILED writing the approved text ({type(ex).__name__}: "
                                 f"{ex}); nothing was sent", ActionClass.SEND, send=p,
                                 by="backend", outcome=Outcome.BACKEND_ERROR_MAYBE_APPLIED)
            self.lease.acted()
            blocked, el = self._action_time(sid, el, approved=True)
            if blocked:
                blocked.action_class, blocked.send = ActionClass.SEND, p
                return blocked
            pairs, err = self._fields_match(p)
            if pairs is None or any(_norm(e.raw_value) != _norm(w) for e, w in pairs):
                return _fail(Outcome.STALE, "stale", err or "the compose fields do not hold the "
                             "approved text; nothing was sent", ActionClass.SEND, send=p)

        def execute(payload):
            return self._perform({"op": "click", "sid": sid, "center": el.center,
                                  "payload": payload, "idempotency_key": p.key})
        try:
            out = self.broker.commit(send_id, execute)
        except SendRefused as e:
            return _fail(Outcome.NOT_ALLOWED, "broker", str(e), ActionClass.SEND, send=p)
        except Exception as e:       # the backend failed mid-send: it may have sent
            return ActResult(False, f"FAILED ({type(e).__name__}: {e}); it may have been sent; "
                             "the key is burned, so this send is never retried automatically",
                             ActionClass.SEND, send=p, by="backend",
                             outcome=Outcome.BACKEND_ERROR_MAYBE_APPLIED)
        finally:
            if p.status in ("committed", "failed"):
                self.lease.acted()
        self._verify_sent(p, sid)
        return ActResult(True, f"sent ({send_id}); observe before the next action",
                         ActionClass.SEND, result=out, send=p, warning=p.warning)

    def _verify_sent(self, p: PreparedSend, send_sid: str) -> None:
        """After the click: the send counts as verified when the compose
        fields no longer hold the payload (cleared or gone)."""
        verified = False
        if self.stop.check() is None:
            try:
                obs = self.observe()
                if obs.kind not in ("refused", "stopped"):
                    parts = p.payload if isinstance(p.payload, dict) else {"": p.payload}
                    still = [k for k, rec in p.fields.items()
                             if (e := obs.element(rec["sid"])) is not None
                             and _norm(e.raw_value) == _norm(parts.get(k)) and _norm(parts.get(k))]
                    verified = bool(p.fields) and not still
            except Exception:
                verified = False
            finally:
                self.lease.invalidate()     # the model has not seen this read
        p.verified = verified
        if not verified:
            p.warning = ("unverified: the screen did not confirm the send (the compose fields "
                         "still hold the text, or could not be read). Check before doing "
                         "anything else: a retry may send a duplicate")
        p.history.append("verified" if verified else "unverified")
