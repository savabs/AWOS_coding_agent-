"""
interrupts.py — known, benign macOS / Safari interruptions over our window, dismissed safely.

Live evidence (GitHub macOS runner, dev_dashboard demo): after signing in, Safari's
"Save Password?" sheet and the system "Allow "Python" to find devices on local networks?"
alert sat over our window, so it was no longer the key window and the agent (correctly)
refused to type the 2FA code. This module clears such interruptions, and nothing else:

    h = InterruptHandler(rt)
    rep = h.handle(snap, refocus=..., key_ok=...)
    rep.handled      # [{"rule", "owner", "structure", "buttons", "button", "outcome", "title"}]
    rep.needs_user   # "" or why a human has to look (unknown dialog, unsafe-only choices, keychain)

Recognition is structural, never fuzzy text:
  * the OWNER process's bundle id (Safari for its own sheets; UserNotificationCenter for
    system privacy alerts; SecurityAgent for keychain / authorization prompts);
  * the STRUCTURE: an AXSheet, or an AXWindow whose subrole is a dialog, with no web area
    inside (a web page's own modal is page content, not an interruption);
  * the EXACT set of button titles (window chrome excluded, typographic quotes normalised).

Every rule names the SAFE choice; nothing else is ever pressed:
    Safari "Save Password?"           -> "Not Now" (never "Save Password"; "Never for This
                                         Website" only when the policy says so)
    Safari "allow notifications"      -> "Don't Allow"
    system privacy alert (e.g. Local  -> "Don't Allow" (our targets are loopback-only)
    Network) from UserNotificationCenter
    keychain / SecurityAgent prompt   -> "Cancel" / "Deny", and the run still stops: needs_user
Anything that does not match a rule exactly -> needs_user with a description; nothing is
clicked. Presses go through AXPress only (no synthetic click on a system dialog).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Sequence, Tuple

SAFARI = "com.apple.Safari"
USER_NOTIFICATION_CENTER = "com.apple.UserNotificationCenter"
SECURITY_AGENT = "com.apple.SecurityAgent"
# Processes that put system alerts on screen. Their dialogs are always looked at; only the
# ones a rule names are ever answered.
SYSTEM_ALERT_OWNERS = frozenset({USER_NOTIFICATION_CENTER, SECURITY_AGENT, "com.apple.coreservices.uiagent",
                                 "com.apple.CoreServicesUIAgent", "com.apple.accessibility.universalAccessAuthWarn",
                                 "com.apple.UserNotificationCenterAgent"})
DIALOG_SUBROLES = frozenset({"AXDialog", "AXSystemDialog", "AXFloatingWindow", "AXSystemFloatingWindow"})
CHROME_SUBROLES = frozenset({"AXCloseButton", "AXMinimizeButton", "AXZoomButton", "AXFullScreenButton",
                             "AXToolbarButton"})
MAX_DIALOG_NODES = 200
MAX_ROUNDS = 4
GONE_WAIT_S = 2.0
FOCUS_WAIT_S = 3.0


def norm_title(s: Any) -> str:
    """Exact button title, only typographic noise removed (curly quotes, spaces, ellipsis)."""
    t = str(s or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    t = re.sub(r"\s+", " ", t).strip()
    return re.sub(r"(…|\.\.\.)$", "", t).strip()


@dataclass(frozen=True)
class Rule:
    name: str
    owners: FrozenSet[str]
    button_sets: Tuple[FrozenSet[str], ...]
    safe: Tuple[str, ...]                    # preference order; the first one present is pressed
    outcome: str = "dismissed"               # "dismissed" (run continues) | "needs_user" (cancel, then stop)
    structures: FrozenSet[str] = frozenset({"sheet", "dialog"})
    about: str = ""


def _fs(*xs: str) -> FrozenSet[str]:
    return frozenset(xs)


SAVE_PASSWORD = Rule(
    "safari_save_password", _fs(SAFARI),
    (_fs("Save Password", "Never for This Website", "Not Now"), _fs("Save Password", "Not Now")),
    ("Not Now",), about="Safari offered to save the password")
SAFARI_PERMISSION = Rule(
    "safari_allow_notifications", _fs(SAFARI), (_fs("Allow", "Don't Allow"),), ("Don't Allow",),
    about="a website asked Safari for a permission (e.g. notifications)")
SYSTEM_PRIVACY = Rule(
    "system_privacy_alert", _fs(USER_NOTIFICATION_CENTER), (_fs("Allow", "Don't Allow"),), ("Don't Allow",),
    about="a system privacy alert (e.g. Local Network access; our targets are loopback-only)")
KEYCHAIN = Rule(
    "keychain_prompt", _fs(SECURITY_AGENT),
    (_fs("Cancel", "OK"), _fs("Cancel", "Allow"), _fs("Deny", "Allow"), _fs("Deny", "Allow", "Always Allow"),
     _fs("Cancel", "Unlock"), _fs("Cancel", "Modify Settings"), _fs("Cancel", "Update Settings"),
     _fs("Cancel", "Use Password"), _fs("Cancel", "Allow", "Always Allow")),
    ("Cancel", "Deny"), outcome="needs_user", structures=frozenset({"sheet", "dialog", "window"}),
    about="a keychain / authorization prompt (cancelled; a human must decide)")
DEFAULT_RULES: Tuple[Rule, ...] = (SAVE_PASSWORD, SAFARI_PERMISSION, SYSTEM_PRIVACY, KEYCHAIN)


@dataclass
class Dialog:
    owner: str
    owner_name: str
    pid: Optional[int]
    element: Any
    structure: str                           # sheet | dialog | window
    buttons: Dict[str, Any]                  # normalised title -> AX element
    title: str = ""
    text: str = ""                           # description only, never used for matching

    def describe(self) -> dict:
        return {"owner": self.owner, "owner_name": self.owner_name, "structure": self.structure,
                "buttons": sorted(self.buttons), "title": self.title[:120], "text": self.text[:240]}


@dataclass
class InterruptReport:
    handled: List[dict] = field(default_factory=list)
    needs_user: str = ""
    unknown: Optional[dict] = None
    focus_ok: Optional[bool] = None          # None: nothing handled, focus not re-checked

    @property
    def seen(self) -> bool:
        return bool(self.handled or self.needs_user)


class InterruptHandler:
    def __init__(self, rt, *, rules: Sequence[Rule] = DEFAULT_RULES, never_save_for_site: bool = False,
                 poll: float = 0.15, log: Optional[Callable[[str], None]] = None):
        self.rt = rt
        self.rules = tuple(rules)
        self.never_save_for_site = never_save_for_site
        self.poll = poll
        self._log = log

    # ------------------------------------------------------------- reading --
    def _read(self, el, names) -> dict:
        try:
            return self.rt.read(el, names) or {}
        except Exception:
            return {}

    def _kids(self, el) -> list:
        try:
            return list(self.rt.children(el))
        except Exception:
            return []

    def _same(self, a, b) -> bool:
        try:
            return a is not None and b is not None and bool(self.rt.same(a, b))
        except Exception:
            return False

    def _apps(self) -> List[Dict[str, Any]]:
        f = getattr(self.rt, "all_apps", None)
        try:
            return list(f() if callable(f) else self.rt.running_apps())
        except Exception:
            return []

    def _windows(self, pid: int) -> list:
        try:
            return list(self._read(self.rt.app_element(pid), ("AXWindows",)).get("AXWindows") or [])
        except Exception:
            return []

    def _structure(self, el) -> Optional[str]:
        r = self._read(el, ("AXRole", "AXSubrole"))
        if r.get("AXRole") == "AXSheet":
            return "sheet"
        if r.get("AXRole") == "AXWindow":
            return "dialog" if r.get("AXSubrole") in DIALOG_SUBROLES else "window"
        return None

    def _dialog(self, el, structure: str, app: Dict[str, Any]) -> Optional[Dialog]:
        """Buttons + a short description of a dialog; None when it holds a web area (page content)."""
        buttons: Dict[str, Any] = {}
        texts: List[str] = []
        stack, n = [el], 0
        while stack and n < MAX_DIALOG_NODES:
            e = stack.pop()
            n += 1
            r = self._read(e, ("AXRole", "AXSubrole", "AXTitle", "AXDescription", "AXValue"))
            role = r.get("AXRole")
            if role == "AXWebArea":
                return None
            if role == "AXButton" and r.get("AXSubrole") not in CHROME_SUBROLES:
                t = norm_title(r.get("AXTitle") or r.get("AXDescription"))
                if t:
                    buttons.setdefault(t, e)
                continue
            if role == "AXStaticText" and isinstance(r.get("AXValue"), str) and len(texts) < 4:
                texts.append(norm_title(r["AXValue"]))
            if e is not el and role in ("AXSheet", "AXWindow"):
                continue                                  # a nested sheet is its own dialog
            stack.extend(reversed(self._kids(e)))
        title = norm_title(self._read(el, ("AXTitle",)).get("AXTitle"))
        return Dialog(str(app.get("bundle_id") or ""), str(app.get("name") or ""), app.get("pid"), el, structure,
                      buttons, title, " | ".join(t for t in texts if t))

    def scan(self, snap) -> List[Dialog]:
        """Dialogs that may sit over our window: system alerts first (they float above every
        app), then sheets / dialog windows of our app, then the frontmost app's focused window
        when it is a dialog. Our own window and other standard windows are never candidates."""
        ours = snap.window
        pid = snap.pid
        out: List[Dialog] = []
        seen: List[Any] = []

        def add(el, app, allow_window=False):
            if el is None or self._same(el, ours) or any(self._same(el, s) for s in seen):
                return
            st = self._structure(el)
            if st is None or (st == "window" and not allow_window):
                return
            d = self._dialog(el, st, app)
            if d is not None:
                seen.append(el)
                out.append(d)

        apps = self._apps()
        for a in apps:
            if a.get("bundle_id") in SYSTEM_ALERT_OWNERS and a.get("pid") is not None and a.get("pid") != pid:
                for w in self._windows(a["pid"]):
                    add(w, a, allow_window=True)
        if pid is not None:
            me = dict(snap.app or {})
            me.setdefault("pid", pid)
            for k in self._kids(ours) if ours is not None else ():
                if self._read(k, ("AXRole",)).get("AXRole") == "AXSheet":
                    add(k, me)
            app_el = self.rt.app_element(pid)
            r = self._read(app_el, ("AXFocusedWindow",))
            add(r.get("AXFocusedWindow"), me)
            for w in self._windows(pid):
                add(w, me)
        try:
            front = self.rt.frontmost() or {}
        except Exception:
            front = {}
        fp = front.get("pid")
        if fp is not None and fp != pid and front.get("bundle_id") not in SYSTEM_ALERT_OWNERS:
            info = next((a for a in apps if a.get("pid") == fp), front)
            add(self._read(self.rt.app_element(fp), ("AXFocusedWindow",)).get("AXFocusedWindow"), info)
        return out

    # ------------------------------------------------------------ matching --
    def match(self, d: Dialog) -> Tuple[Optional[Rule], Optional[str]]:
        got = frozenset(d.buttons)
        for rule in self.rules:
            if d.owner not in rule.owners or d.structure not in rule.structures or got not in rule.button_sets:
                continue
            safe = rule.safe
            if rule is SAVE_PASSWORD and self.never_save_for_site and "Never for This Website" in got:
                safe = ("Never for This Website",)
            button = next((b for b in safe if b in got), None)
            if button is not None:
                return rule, button
        return None, None

    # ------------------------------------------------------------- acting --
    def _gone(self, d: Dialog, snap) -> bool:
        t_end = self.rt.now() + GONE_WAIT_S
        while True:
            if not any(self._same(x.element, d.element) for x in self.scan(snap)):
                return True
            if self.rt.now() >= t_end:
                return False
            self.rt.sleep(self.poll)

    def log(self, msg: str) -> None:
        if self._log:
            self._log(msg)

    def handle(self, snap, *, refocus: Optional[Callable[[], Any]] = None,
               key_ok: Optional[Callable[[], bool]] = None) -> InterruptReport:
        rep = InterruptReport()
        for _ in range(MAX_ROUNDS):
            found = self.scan(snap)
            if not found:
                break
            d = found[0]
            rule, button = self.match(d)
            if rule is None:
                rep.unknown = d.describe()
                rep.needs_user = (f"an unrecognised dialog from {d.owner or d.owner_name or '?'} ({d.structure}, "
                                  f"buttons {sorted(d.buttons)}"
                                  + (f", title {d.title!r}" if d.title else "") + ") is over our window; "
                                  "nothing was clicked")
                self.log(f"interruption: needs_user {rep.unknown}")
                return rep
            pressed = self._press(d.buttons[button])
            entry = {"rule": rule.name, "owner": d.owner, "structure": d.structure, "buttons": sorted(d.buttons),
                     "button": button, "title": d.title[:120], "about": rule.about,
                     "outcome": rule.outcome if pressed else "press_failed"}
            if pressed and not self._gone(d, snap):
                entry["outcome"] = "still_present"
            rep.handled.append(entry)
            self.log(f"interruption: {rule.name} ({d.owner}) -> pressed {button!r}: {entry['outcome']}")
            if entry["outcome"] != "dismissed":
                rep.unknown = d.describe()
                rep.needs_user = (f"{rule.about}: pressed {button!r}" if entry["outcome"] == "needs_user"
                                  else f"{rule.about}: could not dismiss it with {button!r} ({entry['outcome']})")
                return rep
        else:
            if self.scan(snap):
                rep.needs_user = f"interruptions kept appearing over our window ({MAX_ROUNDS} dismissed)"
                return rep
        if rep.handled:
            rep.focus_ok = self._refocus(refocus, key_ok)
            if not rep.focus_ok:
                self.log("interruption: our window did not become key again after the dismissal")
        return rep

    def _press(self, el) -> bool:
        try:
            return "AXPress" in self.rt.actions(el) and bool(self.rt.perform(el, "AXPress"))
        except Exception:
            return False

    def _refocus(self, refocus, key_ok) -> bool:
        if refocus is not None:
            try:
                refocus()
            except Exception:
                pass
        if key_ok is None:
            return True
        t_end = self.rt.now() + FOCUS_WAIT_S
        while True:
            if key_ok():
                return True
            if self.rt.now() >= t_end:
                return False
            self.rt.sleep(self.poll)


__all__ = ["InterruptHandler", "InterruptReport", "Dialog", "Rule", "DEFAULT_RULES", "SAVE_PASSWORD",
           "SAFARI_PERMISSION", "SYSTEM_PRIVACY", "KEYCHAIN", "SYSTEM_ALERT_OWNERS", "norm_title"]
