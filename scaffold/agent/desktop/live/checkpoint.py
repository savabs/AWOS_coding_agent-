"""
checkpoint.py — the human checkpoint for real portals (Month-end Document Pack v0.1).

    cp = Checkpoint(source, clock=rt)                 # source: pack.policy.SourceConfig
    cp.detect(snap)            # Detection("login", "password field") or None
    cp.check(snap)             # raises HumanNeeded(kind, source, url_host)
    snap = cp.wait(observe, hn)   # notify + digest line, poll every 5 s (default 10 min) until the
                                  # page is no longer a login / OTP / captcha page and is on an
                                  # allowed domain -> the fresh Snapshot; else raises HumanTimeout

    pa = PackActor(actor, source)  # actions.Actor wrapped with the read-only policy, the
                                   # checkpoint and human pacing; a drop-in for driver.py

What needs the owner (``KINDS``): a login page (password field, "Sign in" heading with a text
field), an OTP / 2FA prompt (one-time / verification code fields, "OTP"), a captcha (captcha
images / iframes, "I'm not a robot", the word captcha), a session-expired page, an
unusual-activity / security warning, and a page that landed outside the source's allowlist
(``foreign_domain``: an immediate stop, no polling -- the pack never follows a redirect off
the list). The agent never types into password / OTP / captcha fields and never clicks a
captcha widget; it waits for the owner, who logs in, solves the check and enters the code
themselves in their own Safari session (docs/research/india_2026-10-22/documents_brief.md (d)).

Notifications are posted with ``osascript display notification`` only inside the guest VM
(``in_guest_vm``); on the host they are skipped. Digest lines and notifications carry the
source name and the URL HOST only, never a full URL (query strings can hold tokens).

Pacing: ``Pacer`` keeps a random 1.5-3 s gap (configurable) between the end of one action and
the start of the next, measured on the runtime's clock (instant in tests).
"""
from __future__ import annotations

import random
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Sequence, Tuple, Union

from ..ax_compress import norm_role
from ..grounding import Target
from ..pack import policy as P
from ..secrets import handles_in
from .actions import NEEDS_USER, ActionResult, Outcome, Revealed
from .observe import Snapshot

KINDS = ("foreign_domain", "captcha", "otp", "unusual_activity", "session_expired", "login")
DEFAULT_POLL_S = 5.0
DEFAULT_TIMEOUT_S = 600.0
DEFAULT_PACE = (1.5, 3.0)
TEXT_ENTRY = frozenset({"textfield", "textarea", "searchfield", "combobox", "securetextfield"})

CAPTCHA_RE = re.compile(
    r"captcha|i'?m not a robot|i am not a robot|are you a robot|verify (that )?you(')?(re| are) (a )?human|"
    r"human verification|confirm you are human|press (and|&) hold|checking your browser|"
    r"turnstile|challenges\.cloudflare\.com|arkoselabs|funcaptcha", re.I)
OTP_RE = re.compile(
    r"\botp\b|one[- ]?time (pass ?(word|code)|code|pin)|verification code|authenticat(ion|or)( app)? code|"
    r"security code|\b2fa\b|two[- ](factor|step)|2-step|multi[- ]factor|\bmfa\b|enter (the|your) (\d-digit )?code|"
    r"\d-digit code|code (we|was) sent|sent (a|the) code|passcode", re.I)
SESSION_RE = re.compile(
    r"session (has )?(expired|timed? ?out|ended)|your session (is )?(expired|over)|"
    r"(you have been|you've been|you were) (logged|signed) out|(logged|signed) out (due to|because of) inactivity|"
    r"timed out due to inactivity|please (log|sign) ?in again", re.I)
UNUSUAL_RE = re.compile(
    r"unusual (activity|sign-?in|login|traffic)|suspicious (activity|sign-?in|login)|"
    r"verify (it'?s|it is) you|we noticed something unusual|account (has been |is )?(locked|suspended|blocked)|"
    r"temporarily (locked|blocked|restricted)|too many (failed )?(attempts|requests)|access denied|"
    r"new device (detected|sign-?in)", re.I)
LOGIN_RE = re.compile(
    r"\b(sign ?in|log ?in|login|sign on|log on)\b|choose an account|enter (your )?password|welcome back", re.I)
LOGGED_IN_RE = re.compile(r"\b(sign ?out|log ?out|log off)\b", re.I)
PASSWORD_HINT_RE = re.compile(r"pass ?word|\bpin\b|mpin|passcode", re.I)

NOTICE = {
    "login": "log in to {source}",
    "otp": "enter the one-time code for {source}",
    "captcha": "complete the check on {source}",
    "session_expired": "log in again to {source}",
    "unusual_activity": "check a security warning on {source}",
    "foreign_domain": "{source} went to {host}, which is not on its allowlist",
}


# ------------------------------------------------------------------- results --
class HumanNeeded(Exception):
    """The page needs the owner (login, OTP, captcha, session expired, security warning, a
    landing off the allowlist). Carries the source name and the URL host only."""

    def __init__(self, kind: str, source: str, url_host: str, evidence: str = ""):
        self.kind, self.source, self.url_host, self.evidence = kind, source, url_host, evidence
        super().__init__(self.notice())

    def notice(self) -> str:
        return "AWOS needs you: " + NOTICE.get(self.kind, "check {source}").format(
            source=self.source, host=self.url_host or "?")

    def to_dict(self) -> dict:
        return {"kind": self.kind, "source": self.source, "url_host": self.url_host, "evidence": self.evidence}


class HumanTimeout(HumanNeeded):
    """The owner did not clear the checkpoint in time -> the run stops with needs_you."""

    def __init__(self, kind: str, source: str, url_host: str, evidence: str = "", waited_s: float = 0.0):
        self.waited_s = waited_s
        super().__init__(kind, source, url_host, evidence)


@dataclass(frozen=True)
class Detection:
    kind: str
    evidence: str


# ----------------------------------------------------------------- detection --
def _node_texts(n: dict) -> List[str]:
    out = []
    for k in ("title", "description", "placeholder", "help", "identifier", "url"):
        v = n.get(k)
        if isinstance(v, str) and v:
            out.append(v)
    v = n.get("value")
    if isinstance(v, str) and v and not n.get("secure"):
        out.append(v)
    return out


def _walk(n: dict):
    stack = [n]
    while stack:
        x = stack.pop()
        yield x
        stack.extend(reversed(x.get("children") or []))


def _page_roots(snap: Snapshot) -> List[dict]:
    """The web areas when there are any (so Safari's toolbar / address field never count),
    else the whole window."""
    paths = list(snap.web_paths or []) or snap.find(lambda n: norm_role(n.get("role")) == "webarea")
    roots = [r for r in (snap.node(p) for p in paths) if r]
    return roots or ([snap.tree] if snap.tree else [])


def _is_password_field(n: dict, role: str) -> bool:
    if role == "securetextfield" or n.get("secure") is True or str(n.get("subrole") or "") == "AXSecureTextField":
        return True
    if role in TEXT_ENTRY:
        return bool(PASSWORD_HINT_RE.search(" ".join(_node_texts(n)[:4])))
    return False


def is_captcha_node(n: dict) -> bool:
    return bool(CAPTCHA_RE.search(" ".join(_node_texts(n))))


def detect(snap: Optional[Snapshot], source: Optional[P.SourceConfig] = None) -> Optional[Detection]:
    """What on this page needs the owner, or None. Order: foreign domain, captcha, OTP,
    unusual activity, session expired, login."""
    if snap is None or not snap.ok:
        return None
    if source is not None and snap.url:
        d = P.allowed(snap.url, "landed", source)
        if not d.ok:
            return Detection("foreign_domain", d.reason)
    texts: List[str] = []
    headings: List[str] = [snap.window_title or ""]
    fields: List[Tuple[dict, str]] = []
    password = ""
    captcha = ""
    for root in _page_roots(snap):
        for n in _walk(root):
            role = norm_role(n.get("role"))
            t = _node_texts(n)
            texts.extend(t)
            if role == "heading" or str(n.get("subrole") or "") in ("AXApplicationAlert", "AXApplicationDialog",
                                                                    "AXApplicationAlertDialog"):
                headings.extend(t)
                headings.extend(x for k in (n.get("children") or []) for x in _node_texts(k))
            if role in TEXT_ENTRY:
                fields.append((n, role))
                if not password and _is_password_field(n, role):
                    password = " ".join(t[:1]) or "password field"
            if not captcha and is_captcha_node(n):
                captcha = (t[0] if t else role)[:80]
    page = "\n".join(texts)
    if captcha:
        return Detection("captcha", captcha)
    for n, role in fields:
        hint = " ".join(_node_texts(n)[:4])
        if not _is_password_field(n, role) and OTP_RE.search(hint):
            return Detection("otp", f"code field {hint[:60]!r}")
    m = OTP_RE.search(page)
    if m and any(not _is_password_field(n, r) for n, r in fields) and not password:
        return Detection("otp", m.group(0))
    m = UNUSUAL_RE.search(page)
    if m:
        return Detection("unusual_activity", m.group(0))
    m = SESSION_RE.search(page)
    if m:
        return Detection("session_expired", m.group(0))
    if password:
        return Detection("login", f"password field {password[:60]!r}")
    hm = next((LOGIN_RE.search(h) for h in headings if h and LOGIN_RE.search(h)), None)
    if hm and fields and not LOGGED_IN_RE.search(page):
        return Detection("login", f"heading {hm.group(0)!r}")
    return None


# -------------------------------------------------------------------- clocks --
class _SysClock:
    def now(self) -> float:
        return time.monotonic()

    def sleep(self, s: float) -> None:
        time.sleep(max(0.0, s))


def mac_notify(title: str, message: str, *, vm_check: Optional[Callable[[], bool]] = None) -> bool:
    """A macOS notification INSIDE the guest VM only (never on the host). The text goes in as
    osascript argv, never spliced into the script."""
    from . import in_guest_vm
    if not (vm_check or in_guest_vm)():
        return False
    script = ["-e", "on run argv", "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
              "-e", "end run"]
    try:
        return subprocess.run(["osascript", *script, title, message], capture_output=True,
                              timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


class Pacer:
    """Human pace: a random gap of [min_s, max_s] seconds between the end of one action and
    the start of the next."""

    def __init__(self, min_s: float = DEFAULT_PACE[0], max_s: float = DEFAULT_PACE[1], *, clock: Any = None,
                 rng: Optional[random.Random] = None):
        if min_s < 0 or max_s < min_s:
            raise ValueError("pacing needs 0 <= min_s <= max_s")
        self.min_s, self.max_s = min_s, max_s
        self.clock = clock or _SysClock()
        self.rng = rng or random.Random()
        self.last: Optional[float] = None
        self.waits: List[float] = []

    def wait(self) -> float:
        if self.last is None:
            return 0.0
        gap = self.rng.uniform(self.min_s, self.max_s)
        left = self.last + gap - self.clock.now()
        if left > 0:
            self.clock.sleep(left)
            self.waits.append(left)
            return left
        return 0.0

    def done(self) -> None:
        self.last = self.clock.now()


# ---------------------------------------------------------------- checkpoint --
class Checkpoint:
    def __init__(self, source: P.SourceConfig, *, notify: Optional[Callable[[str, str], Any]] = None,
                 digest: Optional[Callable[[str], Any]] = None, poll_s: float = DEFAULT_POLL_S,
                 timeout_s: float = DEFAULT_TIMEOUT_S, clock: Any = None,
                 log: Optional[Callable[[str], None]] = None):
        self.source = source
        self._notify = notify if notify is not None else (lambda title, msg: mac_notify(title, msg))
        self._digest = digest
        self.poll_s, self.timeout_s = poll_s, timeout_s
        self.clock = clock or _SysClock()
        self._log = log
        self.digest_lines: List[str] = []
        self.events: List[dict] = []

    def log(self, msg: str) -> None:
        if self._log:
            self._log(msg)

    def detect(self, snap: Optional[Snapshot]) -> Optional[Detection]:
        return detect(snap, self.source)

    def human_needed(self, snap: Optional[Snapshot], det: Detection) -> HumanNeeded:
        return HumanNeeded(det.kind, self.source.name, P.url_host(snap.url if snap else None), det.evidence)

    def check(self, snap: Optional[Snapshot]) -> None:
        det = self.detect(snap)
        if det is not None:
            raise self.human_needed(snap, det)

    def cleared(self, snap: Optional[Snapshot]) -> bool:
        if snap is None or not snap.ok:
            return False
        if snap.url and not P.allowed(snap.url, "landed", self.source).ok:
            return False
        return self.detect(snap) is None

    def announce(self, hn: HumanNeeded) -> None:
        """The notification inside the VM + a digest line (source + host only)."""
        try:
            self._notify("AWOS needs you", hn.notice())
        except Exception as e:  # noqa: BLE001 -- a failed notification must not end the run
            self.log(f"checkpoint: notification failed ({type(e).__name__})")
        self.digest(f"needs_you: {hn.kind} on {hn.source} ({hn.url_host or '?'}): {hn.notice()}")
        self.events.append(dict(hn.to_dict(), outcome="waiting"))

    def digest(self, line: str) -> None:
        self.digest_lines.append(line)
        if self._digest is not None:
            try:
                self._digest(line)
            except Exception:  # noqa: BLE001
                pass
        self.log(line)

    def wait(self, observe: Callable[[], Optional[Snapshot]], hn: HumanNeeded) -> Snapshot:
        """Stop acting, tell the owner, poll until the page is clear. -> the clear Snapshot.
        A landing off the allowlist is not waited out: HumanTimeout at once."""
        self.announce(hn)
        t0 = self.clock.now()
        if hn.kind == "foreign_domain":
            self.events.append(dict(hn.to_dict(), outcome="stopped"))
            raise HumanTimeout(hn.kind, hn.source, hn.url_host, hn.evidence, 0.0)
        while True:
            waited = self.clock.now() - t0
            if waited >= self.timeout_s:
                self.digest(f"needs_you: {hn.kind} on {hn.source} not cleared after {int(waited)} s; run stopped")
                self.events.append(dict(hn.to_dict(), outcome="timeout", waited_s=round(waited, 1)))
                raise HumanTimeout(hn.kind, hn.source, hn.url_host, hn.evidence, waited)
            self.clock.sleep(min(self.poll_s, max(0.0, self.timeout_s - waited)))
            try:
                snap = observe()
            except Exception:  # noqa: BLE001 -- the page may be mid-load
                snap = None
            if self.cleared(snap):
                waited = self.clock.now() - t0
                self.digest(f"resumed: {hn.kind} on {hn.source} cleared after {int(waited)} s")
                self.events.append(dict(hn.to_dict(), outcome="resumed", waited_s=round(waited, 1)))
                return snap  # type: ignore[return-value]

    def guard(self, snap: Optional[Snapshot], observe: Callable[[], Optional[Snapshot]]
              ) -> Tuple[Optional[Snapshot], Optional[HumanNeeded]]:
        """snap is clear -> (snap, None); else wait for the owner -> (fresh snap, the HumanNeeded
        that was cleared). Raises HumanTimeout."""
        det = self.detect(snap)
        if det is None:
            return snap, None
        hn = self.human_needed(snap, det)
        return self.wait(observe, hn), hn


# ----------------------------------------------------------------- PackActor --
class PackActor:
    """actions.Actor with the read-only pack policy, the human checkpoint and human pacing.
    Duck-types Actor (everything not overridden is delegated), so MacBrowserBackend / MacEnv
    take it as ``actor=``. Refusals carry no approval path: ``approve`` is never consulted
    for a deny-listed control."""

    def __init__(self, actor: Any, source: P.SourceConfig, *, checkpoint: Optional[Checkpoint] = None,
                 pacer: Optional[Pacer] = None, pace: Tuple[float, float] = DEFAULT_PACE,
                 poll_s: float = DEFAULT_POLL_S, timeout_s: float = DEFAULT_TIMEOUT_S,
                 notify: Optional[Callable[[str, str], Any]] = None, digest: Optional[Callable[[str], Any]] = None):
        object.__setattr__(self, "actor", actor)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "checkpoint", checkpoint or Checkpoint(
            source, notify=notify, digest=digest, poll_s=poll_s, timeout_s=timeout_s, clock=actor.rt,
            log=getattr(actor, "_log", None)))
        object.__setattr__(self, "pacer", pacer or Pacer(*pace, clock=actor.rt))
        if getattr(actor, "url_policy", None) is None:
            actor.url_policy = P.url_hook(source)          # the actions.url_allowed hook

    def __getattr__(self, name: str) -> Any:
        return getattr(self.actor, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self.actor, name, value)

    # ------------------------------------------------------------ helpers --
    def _refuse(self, action: str, reason: str, after: Optional[Snapshot] = None, **detail) -> ActionResult:
        r = self.actor._refuse(action, reason, **detail)
        r.after = after
        return r

    def _host_ok(self, action: str) -> Optional[ActionResult]:
        if not self.actor.vm_check():
            return self._refuse(action, "live UI control runs only inside the guest VM (host Mac refused)")
        return None

    def _needs_you(self, action: str, hn: HumanNeeded, after: Optional[Snapshot]) -> ActionResult:
        return self._refuse(action, f"{NEEDS_USER} needs_you: {hn.notice()}", after, needs_you=hn.to_dict())

    def _pre(self, action: str, snap: Snapshot) -> Tuple[Optional[Snapshot], Optional[ActionResult], str]:
        """-> (snap to act on, refusal, cleared kind)."""
        try:
            fresh, hn = self.checkpoint.guard(snap, lambda: self.actor.reobserve(snap))
        except HumanTimeout as t:
            return None, self._needs_you(action, t, snap), ""
        return fresh, None, (hn.kind if hn else "")

    def _post(self, r: ActionResult) -> ActionResult:
        if r.outcome is Outcome.REFUSED and "landed_host" in r.detail:
            hn = HumanNeeded("foreign_domain", self.source.name, str(r.detail.get("landed_host") or ""),
                             "redirected off the allowlist")
            self.checkpoint.announce(hn)
            self.checkpoint.events.append(dict(hn.to_dict(), outcome="stopped"))
            r.detail["needs_you"] = hn.to_dict()
            return r
        if r.after is None:
            return r
        after = r.after
        try:
            fresh, hn = self.checkpoint.guard(after, lambda: self.actor.reobserve(after))
        except HumanTimeout as t:
            return self._needs_you(r.action, t, after)
        if hn is not None:
            r.after = fresh
            r.detail["human_checkpoint"] = hn.kind
        return r

    def _act(self, fn: Callable[[], ActionResult]) -> ActionResult:
        self.pacer.wait()
        try:
            r = fn()
        finally:
            self.pacer.done()
        return self._post(r)

    @staticmethod
    def _same_control(old: Snapshot, new: Snapshot, path: tuple) -> bool:
        a, b = old.node(path) or {}, new.node(path)
        return (b is not None and new.element(path) is not None
                and norm_role(a.get("role")) == norm_role(b.get("role"))
                and P.control_name(a) == P.control_name(b))

    def _target(self, action: str, snap: Snapshot, target: Any, verdict: Callable[[dict], Optional[str]]
                ) -> Tuple[Optional[Snapshot], Optional[tuple], Optional[ActionResult], str]:
        """Locate, refuse by policy at once (never after a 10-minute wait), then the human
        checkpoint, then re-locate / re-check on the fresh page.
        -> (snap, path, refusal, cleared kind)."""
        g = self._host_ok(action)
        if g:
            return None, None, g, ""
        path, err = self.actor.locate(snap, target)
        if path is None:
            if self.checkpoint.detect(snap) is None:
                return None, None, self.actor._refuse(action, err), ""
        else:
            why = verdict(snap.node(path) or {})
            if why:
                return None, None, self._refuse(action, why, snap), ""
        fresh, refused, cleared = self._pre(action, snap)
        if refused:
            return None, None, refused, ""
        if fresh is not snap:
            if not isinstance(target, Target) and not self._same_control(snap, fresh, tuple(target)):
                return None, None, self._refuse(
                    action, f"resumed after {cleared}: the page changed during the human checkpoint; re-observe",
                    fresh, human_checkpoint=cleared), cleared
            snap = fresh
            path, err = self.actor.locate(snap, target)
            if path is None:
                return None, None, self._refuse(action, err, snap, human_checkpoint=cleared), cleared
            why = verdict(snap.node(path) or {})
            if why:
                return None, None, self._refuse(action, why, snap, human_checkpoint=cleared), cleared
        return snap, path, None, cleared

    @staticmethod
    def _mark(r: ActionResult, cleared: str) -> ActionResult:
        if cleared and "human_checkpoint" not in r.detail:
            r.detail["human_checkpoint"] = cleared
        return r

    # ------------------------------------------------------------ actions --
    def _press_verdict(self, node: dict) -> Optional[str]:
        if is_captcha_node(node):
            return "never clicks captcha widgets: the owner solves it"
        if P.human_only_field(node) and norm_role(node.get("role")) in TEXT_ENTRY:
            return "password / OTP / captcha fields are the owner's"
        v = P.control_verdict(node, self.source, "press")
        return "" if v.ok else f"read-only pack: {v.reason}"

    def press(self, snap: Snapshot, target: Union[Target, Sequence[int]], expect: Any = None) -> ActionResult:
        snap2, path, refused, cleared = self._target("press", snap, target, self._press_verdict)
        if refused:
            return refused
        return self._mark(self._act(lambda: self.actor.press(snap2, path, expect)), cleared)

    def _type(self, action: str, snap: Snapshot, target: Any, value: Any, call: Callable) -> ActionResult:
        if isinstance(value, Revealed) or handles_in(str(value)):
            return self._refuse(action, "the pack never types secrets (the owner logs in themselves)", snap)

        def verdict(node: dict) -> Optional[str]:
            v = P.field_verdict(node)
            return "" if v.ok else f"read-only pack: {v.reason}"
        snap2, path, refused, cleared = self._target(action, snap, target, verdict)
        if refused:
            return refused
        return self._mark(self._act(lambda: call(snap2, path)), cleared)

    def set_value(self, snap: Snapshot, target: Any, value: str, expect: Any = None, **kw) -> ActionResult:
        return self._type("set_value", snap, target, value,
                          lambda s, p: self.actor.set_value(s, p, value, expect, **kw))

    def type_text(self, snap: Snapshot, target: Any, value: str, *a, **kw) -> ActionResult:
        return self._type("type_text", snap, target, value, lambda s, p: self.actor.type_text(s, p, value, *a, **kw))

    def select_option(self, snap: Snapshot, target: Any, option: str) -> ActionResult:
        def verdict(node: dict) -> Optional[str]:
            for v in (P.control_verdict(node, self.source, "select"), P.option_verdict(option)):
                if not v.ok:
                    return f"read-only pack: {v.reason}"
            return ""
        snap2, path, refused, cleared = self._target("select_option", snap, target, verdict)
        if refused:
            return refused
        return self._mark(self._act(lambda: self.actor.select_option(snap2, path, option)), cleared)

    def menu_pick(self, app: Any, items: Sequence[str], expect: Any = None) -> ActionResult:
        g = self._host_ok("menu_pick")
        if g:
            return g
        for it in items:
            v = P.option_verdict(it)
            if not v.ok:
                return self._refuse("menu_pick", f"read-only pack: {v.reason}", policy=v.to_dict())
        return self._act(lambda: self.actor.menu_pick(app, items, expect))

    def scroll(self, snap: Snapshot, *a, **kw) -> ActionResult:
        g = self._host_ok("scroll")
        if g:
            return g
        return self._act(lambda: self.actor.scroll(snap, *a, **kw))

    def _url(self, action: str, url: str, call: Callable[[], ActionResult]) -> ActionResult:
        g = self._host_ok(action)
        if g:
            return g
        d = P.allowed(url, "navigate", self.source)
        if not d.ok:
            return self._refuse(action, f"pack allowlist: {d.reason}", url_host=P.url_host(url))
        return self._act(call)

    def open_url(self, url: str, **kw) -> ActionResult:
        return self._url("open_url", url, lambda: self.actor.open_url(url, **kw))

    def navigate(self, window: Any, url: str, **kw) -> ActionResult:
        return self._url("navigate", url, lambda: self.actor.navigate(window, url, **kw))


__all__ = ["Checkpoint", "DEFAULT_PACE", "DEFAULT_POLL_S", "DEFAULT_TIMEOUT_S", "Detection", "HumanNeeded",
           "HumanTimeout", "KINDS", "PackActor", "Pacer", "detect", "is_captcha_node", "mac_notify"]
