"""
actions.py — verified UI actions over a live AX runtime.

    actor = Actor(rt, approve=None, secrets=broker)        # approve=None: destructive = denied
    snap = actor.observe("Safari")
    r = actor.press(snap, Target(role="button", name="Sign in"), expect=Expect(url_changes=True))
    r.outcome          # Outcome.VERIFIED | UNVERIFIED | REFUSED
    r.after            # the re-observed Snapshot

Every action:
  1. gates: the host-Mac guard (live control only runs inside the guest VM, see
     __init__.in_guest_vm), the app allowlist (default Safari + TextEdit), the existing
     app_policy.AppPolicy tier for the action kind, element enabled, and the destructive
     gate: ax_compress.button_role(node) == "destructive" needs ``approve(request)`` to
     return True (no callback -> REFUSED);
  2. acts the least invasive way: AXPress before a CGEvent click at the element's visible
     centre (only when the point really belongs to the target app); AXValue before focus +
     typed keystrokes (CGEventKeyboardSetUnicodeString, posted to the app's pid only);
  3. re-observes and verifies the expected change (value equals, focus moved, URL
     changed / equals, element appeared / disappeared, selection, any change). Nothing
     observed changing -> UNVERIFIED, never VERIFIED.

Secrets: a secure field only takes a ``{{secret:name}}`` handle (resolved through the
secrets broker at the moment of typing) or a ``Revealed`` value a trusted sink already
resolved. Plain text into a secure field is REFUSED. Results and logs carry the handle
or ``<secret>``, never the value; plain values are reported by length only.

URLs: open_url only opens http(s) on 127.0.0.1 / localhost / ::1 and file:// unless the
URL starts with one of ``allow_urls``. Safari pages always open in a NEW window
(File > New Window via AX, Cmd-N as fallback) or by ``open -a Safari`` when Safari is not
running; an existing window's page is never navigated away.
"""
from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from ..app_policy import AppIdentity, AppPolicy
from ..ax_compress import button_role, diff, is_secure_node, norm_role
from ..grounding import Target, ground
from ..secrets import HANDLE_RE, handles_in
from .observe import (DEFAULT_MAX_NODES, DEFAULT_TIMEOUT_S, Snapshot, observe, snapshot_element,
                      window_list)

SAFARI = "com.apple.Safari"
TEXTEDIT = "com.apple.TextEdit"
DEFAULT_ALLOWED_APPS = frozenset({SAFARI, TEXTEDIT})
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
TEXT_ROLES = {"textfield", "textarea", "searchfield", "combobox", "securetextfield"}
MAX_TEXT = 5000
KEY_RETURN, KEY_A, KEY_N = 36, 0, 45
FLAG_CMD = 1 << 20


class Outcome(str, Enum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    REFUSED = "refused"


class Revealed(str):
    """A secret value a trusted sink already resolved (e.g. micro_agent's fill). Its repr
    never shows the value; results report it as ``<secret>``."""

    def __repr__(self) -> str:
        return "Revealed(<secret>)"


@dataclass
class Expect:
    value: Optional[str] = None            # element value equals (non-secure)
    value_len: Optional[int] = None        # secure field: number of characters
    focused: bool = False                  # the target element holds focus
    url: Optional[str] = None              # web area URL equals (normalised)
    url_changes: bool = False
    appears: Union[None, str, Target] = None
    disappears: Union[None, str, Target] = None
    selected: Optional[bool] = None        # radio / checkbox / tab state
    any_change: bool = False
    frontmost: Optional[int] = None        # pid
    predicate: Optional[Callable[[Snapshot], bool]] = None

    def empty(self) -> bool:
        return not any((self.value is not None, self.value_len is not None, self.focused, self.url,
                        self.url_changes, self.appears, self.disappears, self.selected is not None,
                        self.any_change, self.frontmost is not None, self.predicate))


@dataclass
class ActionResult:
    outcome: Outcome
    action: str
    reason: str = ""
    method: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)
    after: Optional[Snapshot] = field(default=None, repr=False)
    window: Any = field(default=None, repr=False)

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.VERIFIED

    def to_dict(self) -> dict:
        return {"outcome": self.outcome.value, "action": self.action, "reason": self.reason,
                "method": self.method, "detail": dict(self.detail)}


def norm_url(u: Optional[str]) -> str:
    if not u:
        return ""
    p = urllib.parse.urlsplit(u)
    path = p.path or "/"
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), path, p.query, ""))


def url_allowed(url: str, allow_urls: Iterable[str] = ()) -> Tuple[bool, str]:
    if any(url.startswith(a) for a in allow_urls if a):
        return True, "explicitly allowed"
    p = urllib.parse.urlsplit(url)
    if p.scheme == "file":
        return True, "local file"
    if p.scheme in ("http", "https") and (p.hostname or "") in LOCAL_HOSTS:
        return True, "loopback"
    return False, f"only 127.0.0.1 / localhost / file:// URLs may be opened (got {p.scheme}://{p.hostname})"


def _text_match(n: dict, text: str) -> bool:
    t = text.lower()
    return any(isinstance(n.get(k), str) and t in n[k].lower() for k in ("title", "value", "description"))


def present(snap: Optional[Snapshot], what: Union[str, Target]) -> bool:
    if snap is None or snap.tree is None:
        return False
    if isinstance(what, Target):
        return ground(snap.grounding_input(), what, allow_relaxed=False).ok
    return bool(snap.find(lambda n: _text_match(n, what)))


class Actor:
    def __init__(self, rt, *, approve: Optional[Callable[[dict], bool]] = None, secrets: Any = None,
                 allow_apps: Iterable[str] = DEFAULT_ALLOWED_APPS, policy: Optional[AppPolicy] = None,
                 allow_urls: Iterable[str] = (), settle: float = 4.0, poll: float = 0.15,
                 max_nodes: int = DEFAULT_MAX_NODES, timeout: float = DEFAULT_TIMEOUT_S,
                 use_events: bool = True, log: Optional[Callable[[str], None]] = None,
                 vm_check: Optional[Callable[[], bool]] = None):
        from . import in_guest_vm
        self.rt = rt
        self.approve = approve
        self.secrets = secrets
        self.allow_apps = frozenset(allow_apps)
        self.policy = policy if policy is not None else AppPolicy()
        self.allow_urls = tuple(allow_urls)
        self.settle, self.poll = settle, poll
        self.max_nodes, self.timeout = max_nodes, timeout
        self.use_events = use_events
        self._log = log
        self.vm_check = vm_check if vm_check is not None else in_guest_vm
        self.history: List[dict] = []

    # ------------------------------------------------------------- helpers --
    def log(self, msg: str) -> None:
        if self._log:
            self._log(msg)

    def observe(self, app: Any = None, window: Any = None) -> Snapshot:
        return observe(self.rt, app, window, max_nodes=self.max_nodes, timeout=self.timeout)

    def reobserve(self, snap: Snapshot) -> Snapshot:
        """The same window again (same AX window element)."""
        if snap.window is not None:
            return snapshot_element(self.rt, snap.window, app=snap.app, max_nodes=self.max_nodes,
                                    timeout=self.timeout)
        return self.observe(snap.app or None)

    def locate(self, snap: Snapshot, target: Union[Target, Sequence[int]]) -> Tuple[Optional[tuple], str]:
        if isinstance(target, Target):
            g = ground(snap.grounding_input(), target, allow_relaxed=False)
            if not g.ok:
                return None, f"grounding refused: {g.refusal.reason}: {g.refusal.message}"
            return tuple(g.match.path), ""
        path = tuple(target)
        return (path, "") if snap.node(path) is not None and snap.element(path) is not None else (
            None, f"no element at path {list(path)}")

    def _done(self, r: ActionResult) -> ActionResult:
        self.history.append(r.to_dict())
        self.log(f"{r.action}: {r.outcome.value} {r.method} {r.reason}".rstrip())
        return r

    def _refuse(self, action: str, reason: str, **detail) -> ActionResult:
        return self._done(ActionResult(Outcome.REFUSED, action, reason, detail=detail))

    def _gate(self, action: str, kind: str, app: Dict[str, Any], node: Optional[dict] = None
              ) -> Optional[ActionResult]:
        if not self.vm_check():
            return self._refuse(action, "live UI control runs only inside the guest VM (host Mac refused)")
        bid = str(app.get("bundle_id") or "")
        if bid not in self.allow_apps:
            return self._refuse(action, f"app {bid or app.get('name') or '?'} is not an allowed test target",
                                app=bid)
        rule = self.policy.rule(AppIdentity.from_any(app))
        if not rule.allows_kind(kind):
            return self._refuse(action, f"app policy: {rule.tier.value} ({rule.reason})", app=bid)
        if node is not None:
            if node.get("enabled") is False:
                return self._refuse(action, "element is disabled")
            if kind in ("click", "press", "pick", "select") and button_role(node) == "destructive":
                req = {"kind": "destructive", "app": bid, "role": norm_role(node.get("role")),
                       "name": node.get("title") or node.get("description") or "", "action": action}
                ok = False
                if self.approve is not None:
                    try:
                        ok = bool(self.approve(req))
                    except Exception:
                        ok = False
                if not ok:
                    return self._refuse(action, f"destructive control {req['name']!r} not approved",
                                        destructive=True)
        return None

    def _read_el(self, el, names=("AXValue", "AXFocused", "AXSelected", "AXNumberOfCharacters")):
        return self.rt.read(el, names) if el is not None else {}

    def _check(self, expect: Expect, before: Snapshot, after: Snapshot, el: Any) -> Tuple[bool, str]:
        live = self._read_el(el)
        if expect.value is not None:
            v = live.get("AXValue")
            if v is None or str(v) != expect.value:
                return False, "value differs"
        if expect.value_len is not None:
            n = live.get("AXNumberOfCharacters")
            if n is None:
                v = live.get("AXValue")
                n = len(v) if isinstance(v, str) else None
            if n != expect.value_len:
                return False, "length differs" if n is not None else "length unreadable"
        if expect.focused and live.get("AXFocused") is not True:
            return False, "focus did not move"
        if expect.selected is not None:
            v = live.get("AXValue")
            sel = live.get("AXSelected") is True or v in (1, True, "1")
            if sel != expect.selected:
                return False, "selection differs"
        if expect.url and norm_url(after.url) != norm_url(expect.url):
            return False, f"URL is {after.url!r}"
        if expect.url_changes and norm_url(after.url) == norm_url(before.url):
            return False, "URL did not change"
        if expect.appears is not None and not present(after, expect.appears):
            return False, "expected element did not appear"
        if expect.disappears is not None and present(after, expect.disappears):
            return False, "element did not disappear"
        if expect.frontmost is not None:
            f = self.rt.frontmost() or {}
            if f.get("pid") != expect.frontmost:
                return False, "app is not frontmost"
        if expect.predicate is not None and not expect.predicate(after):
            return False, "predicate false"
        if expect.any_change:
            d = diff(before.compressed(), after.compressed())
            if not d["added"] and not d["removed"] and norm_url(after.url) == norm_url(before.url):
                return False, "nothing changed"
        return True, ""

    def _verify(self, action: str, method: str, before: Snapshot, el: Any, expect: Expect,
                detail: Optional[dict] = None, window: Any = None) -> ActionResult:
        t_end = self.rt.now() + self.settle
        after, why = before, "not checked"
        while True:
            self.rt.sleep(self.poll)
            after = self.reobserve(before) if before.ok else self.observe(before.app or None)
            ok, why = self._check(expect, before, after, el)
            if ok:
                return self._done(ActionResult(Outcome.VERIFIED, action, "", method, detail or {}, after, window))
            if self.rt.now() >= t_end:
                return self._done(ActionResult(Outcome.UNVERIFIED, action, why, method, detail or {}, after,
                                               window))

    def _center(self, node: dict) -> Optional[Tuple[float, float]]:
        f = node.get("frame")
        if not f or f.get("w", 0) <= 0 or f.get("h", 0) <= 0:
            return None
        return f["x"] + f["w"] / 2, f["y"] + f["h"] / 2

    def _cg_click(self, snap: Snapshot, node: dict) -> Tuple[bool, str]:
        if not self.use_events or not self.rt.can_post_events():
            return False, "synthetic events unavailable"
        c = self._center(node)
        if c is None:
            return False, "element has no visible frame"
        hit = self.rt.element_at_point(*c)
        if hit is None or self.rt.pid(hit) != snap.pid:
            return False, "the click point belongs to another app (occluded)"
        self.rt.activate(snap.pid)
        return self.rt.post_click(*c), ""

    # --------------------------------------------------------------- press --
    def press(self, snap: Snapshot, target: Union[Target, Sequence[int]], expect: Optional[Expect] = None
              ) -> ActionResult:
        path, err = self.locate(snap, target)
        if path is None:
            return self._refuse("press", err)
        node, el = snap.node(path), snap.element(path)
        g = self._gate("press", "click", snap.app, node)
        if g:
            return g
        expect = expect if expect is not None and not expect.empty() else Expect(any_change=True)
        detail = {"role": norm_role(node.get("role")), "name": node.get("title", "")}
        if "AXPress" in self.rt.actions(el) and self.rt.perform(el, "AXPress"):
            return self._verify("press", "ax_press", snap, el, expect, detail)
        ok, why = self._cg_click(snap, node)
        if not ok:
            return self._refuse("press", f"AXPress unavailable and CGEvent click refused: {why}", **detail)
        return self._verify("press", "cg_click", snap, el, expect, detail)

    # ----------------------------------------------------------- set value --
    def _resolve_value(self, value: str, node: dict, app: Dict[str, Any]) -> Tuple[Optional[str], str, str]:
        """-> (plain or None, shown, error)"""
        secure = bool(node.get("secure")) or is_secure_node(node, norm_role(node.get("role")))
        if isinstance(value, Revealed):
            return str(value), "<secret>", ""
        if handles_in(value):
            if HANDLE_RE.sub("", value).strip() and secure:
                return None, "", "a secure field takes exactly one secret handle"
            if self.secrets is None:
                return None, "", "no secrets broker to resolve the handle"
            target = f"ui:{app.get('bundle_id', '')}/{node.get('title', '')}"
            try:
                if hasattr(self.secrets, "substitute"):
                    plain = self.secrets.substitute(value, target=target)
                else:
                    plain = HANDLE_RE.sub(lambda m: self.secrets.get(m.group(1)), value)
            except Exception as e:
                return None, "", f"secret unavailable: {type(e).__name__}"
            return plain, value, ""
        if secure:
            return None, "", "secure field: only a {{secret:name}} handle may be typed, never plain text"
        if len(value) > MAX_TEXT:
            return None, "", "value too long"
        return value, f"<{len(value)} chars>", ""

    def set_value(self, snap: Snapshot, target: Union[Target, Sequence[int]], value: str,
                  expect: Optional[Expect] = None, *, allow_typing: bool = True) -> ActionResult:
        path, err = self.locate(snap, target)
        if path is None:
            return self._refuse("set_value", err)
        node, el = snap.node(path), snap.element(path)
        role = norm_role(node.get("role"))
        if role not in TEXT_ROLES:
            return self._refuse("set_value", f"{role} is not a text field")
        g = self._gate("set_value", "set_value", snap.app, node)
        if g:
            return g
        plain, shown, err = self._resolve_value(value, node, snap.app)
        if plain is None:
            return self._refuse("set_value", err, secure=bool(node.get("secure")))
        secure = bool(node.get("secure")) or is_secure_node(node, role)
        if expect is None or expect.empty():
            expect = Expect(value_len=len(plain)) if secure else Expect(value=plain)
        detail = {"role": role, "name": node.get("title", ""), "value": shown, "secure": secure}
        try:
            if self.rt.settable(el, "AXValue") and self.rt.set_attr(el, "AXValue", plain):
                r = self._verify("set_value", "ax_value", snap, el, expect, detail)
                if r.ok or not allow_typing:
                    return r
            if not allow_typing:
                return self._refuse("set_value", "AXValue not settable", **detail)
            return self._type_into(snap, el, node, plain, expect, detail)
        finally:
            del plain

    def _type_into(self, snap: Snapshot, el, node: dict, plain: str, expect: Expect, detail: dict
                   ) -> ActionResult:
        if not self.use_events or not self.rt.can_post_events():
            return self._refuse("set_value", "AXValue failed and synthetic keystrokes are unavailable", **detail)
        self.rt.set_attr(el, "AXFocused", True)
        if self._read_el(el).get("AXFocused") is not True:
            ok, why = self._cg_click(snap, node)
            if not ok or self._read_el(el).get("AXFocused") is not True:
                return self._refuse("set_value", f"could not focus the field ({why or 'focus did not move'})",
                                    **detail)
        self.rt.post_key(snap.pid, KEY_A, FLAG_CMD)          # replace the current content
        self.rt.post_text(snap.pid, plain)
        return self._verify("set_value", "keystrokes", snap, el, expect, detail)

    def type_text(self, snap: Snapshot, target: Union[Target, Sequence[int]], value: str,
                  expect: Optional[Expect] = None) -> ActionResult:
        """Focus + keystrokes only (for fields whose AXValue is not honoured)."""
        path, err = self.locate(snap, target)
        if path is None:
            return self._refuse("type", err)
        node, el = snap.node(path), snap.element(path)
        g = self._gate("type", "type", snap.app, node)
        if g:
            return g
        plain, shown, err = self._resolve_value(value, node, snap.app)
        if plain is None:
            return self._refuse("type", err)
        secure = bool(node.get("secure"))
        if expect is None or expect.empty():
            expect = Expect(value_len=len(plain)) if secure else Expect(value=plain)
        return self._type_into(snap, el, node, plain, expect, {"name": node.get("title", ""), "value": shown})

    # -------------------------------------------------------------- select --
    def select_option(self, snap: Snapshot, target: Union[Target, Sequence[int]], option: str) -> ActionResult:
        """A radio button inside a radio group, or an item of a pop-up button."""
        path, err = self.locate(snap, target)
        if path is None:
            return self._refuse("select", err)
        node = snap.node(path)
        role = norm_role(node.get("role"))
        if role == "radiogroup":
            for p in snap.find(lambda n: norm_role(n.get("role")) == "radiobutton"
                               and (n.get("title") or "").strip().lower() == option.strip().lower()):
                if p[:len(path)] == path:
                    return self.press(snap, p, Expect(selected=True))
            return self._refuse("select", f"no option {option!r}")
        if role == "radiobutton":
            return self.press(snap, path, Expect(selected=True))
        if role == "popupbutton":
            return self._popup(snap, path, option)
        return self._refuse("select", f"{role} is not a choice")

    def _popup(self, snap: Snapshot, path: tuple, option: str) -> ActionResult:
        node, el = snap.node(path), snap.element(path)
        g = self._gate("select", "select", snap.app, node)
        if g:
            return g
        if not self.rt.perform(el, "AXPress"):
            return self._refuse("select", "could not open the pop-up")
        self.rt.sleep(self.poll)
        item = self._find_menu_item(el, option)
        if item is None:
            self.rt.perform(el, "AXCancel")
            return self._refuse("select", f"no option {option!r} in the pop-up")
        g = self._gate("select", "pick", snap.app, {"role": "AXMenuItem", "title": option})
        if g:
            self.rt.perform(el, "AXCancel")
            return g
        self.rt.perform(item, "AXPress")
        return self._verify("select", "ax_menu", snap, el, Expect(value=option), {"option": option})

    def _find_menu_item(self, root, title: str, depth: int = 4):
        stack = [(root, 0)]
        want = title.strip().lower()
        while stack:
            e, d = stack.pop()
            r = self.rt.read(e, ("AXRole", "AXTitle"))
            if r.get("AXRole") == "AXMenuItem" and str(r.get("AXTitle") or "").strip().lower() == want:
                return e
            if d < depth:
                stack.extend((c, d + 1) for c in self.rt.children(e))
        return None

    # ---------------------------------------------------------------- menu --
    def menu_pick(self, app: Any, items: Sequence[str], expect: Optional[Expect] = None) -> ActionResult:
        """Menu bar path, e.g. ("File", "New Window"), through AX only."""
        snap = self.observe(app)
        info = snap.app
        if info.get("pid") is None:
            return self._refuse("menu_pick", "app not running")
        g = self._gate("menu_pick", "pick", info, {"role": "AXMenuItem", "title": items[-1]})
        if g:
            return g
        bar = self.rt.read(self.rt.app_element(info["pid"]), ("AXMenuBar",)).get("AXMenuBar")
        cur = bar
        for i, title in enumerate(items):
            found = None
            stack = [(cur, 0)]
            while stack and found is None:
                e, d = stack.pop()
                for c in self.rt.children(e):
                    r = self.rt.read(c, ("AXRole", "AXTitle"))
                    if r.get("AXRole") in ("AXMenuBarItem", "AXMenuItem") and \
                            str(r.get("AXTitle") or "").strip().lower() == title.strip().lower():
                        found = c
                        break
                    if r.get("AXRole") == "AXMenu" and d < 2:
                        stack.append((c, d + 1))
            if found is None:
                return self._refuse("menu_pick", f"menu item {title!r} not found")
            if i < len(items) - 1:
                cur = found
                continue
            if not self.rt.perform(found, "AXPress"):
                return self._refuse("menu_pick", f"could not press {title!r}")
        exp = expect if expect is not None and not expect.empty() else Expect(any_change=True)
        return self._verify("menu_pick", "ax_menu", snap, None, exp, {"items": list(items)})

    # -------------------------------------------------------------- scroll --
    def scroll(self, snap: Snapshot, target: Union[None, Target, Sequence[int]] = None,
               direction: str = "down", amount: int = 3) -> ActionResult:
        g = self._gate("scroll", "scroll", snap.app)
        if g:
            return g
        if target is not None:
            path, err = self.locate(snap, target)
            if path is None:
                return self._refuse("scroll", err)
            el = snap.element(path)
            if "AXScrollToVisible" in self.rt.actions(el) and self.rt.perform(el, "AXScrollToVisible"):
                return self._verify("scroll", "ax_scroll_to_visible", snap, el, Expect(any_change=True))
        node = snap.tree or {}
        c = self._center(node)
        if c is None or not self.use_events or not self.rt.can_post_events():
            return self._refuse("scroll", "no scroll target / synthetic events unavailable")
        hit = self.rt.element_at_point(*c)
        if hit is None or self.rt.pid(hit) != snap.pid:
            return self._refuse("scroll", "the window centre belongs to another app")
        lines = -abs(amount) if direction == "down" else abs(amount)
        self.rt.scroll_wheel(c[0], c[1], lines)
        return self._verify("scroll", "cg_wheel", snap, None, Expect(any_change=True),
                            {"direction": direction})

    # ------------------------------------------------------------ activate --
    def activate(self, app: Any) -> ActionResult:
        snap = self.observe(app)
        info = snap.app
        if info.get("pid") is None:
            return self._refuse("activate", "app not running")
        g = self._gate("activate", "observe", info)
        if g:
            return g
        self.rt.activate(info["pid"])
        return self._verify("activate", "nsrunningapplication", snap, None, Expect(frontmost=info["pid"]))

    def raise_window(self, snap: Snapshot) -> bool:
        w = snap.window
        if w is None:
            return False
        self.rt.activate(snap.pid)
        self.rt.set_attr(w, "AXMain", True)
        self.rt.perform(w, "AXRaise")
        return True

    # ---------------------------------------------------------------- URLs --
    def _app(self, bundle: str) -> Optional[Dict[str, Any]]:
        return next((a for a in self.rt.running_apps() if a.get("bundle_id") == bundle), None)

    def _web_url(self, win, app) -> Optional[str]:
        return snapshot_element(self.rt, win, app=app, max_nodes=self.max_nodes, timeout=self.timeout).url

    def _wait_window(self, pid: int, pred, timeout: float):
        t_end = self.rt.now() + timeout
        while True:
            for w in window_list(self.rt, pid):
                if pred(w):
                    return w
            if self.rt.now() >= t_end:
                return None
            self.rt.sleep(self.poll)

    def open_url(self, url: str, *, app: str = "Safari", bundle: str = SAFARI,
                 timeout: float = 15.0) -> ActionResult:
        ok, why = url_allowed(url, self.allow_urls)
        if not ok:
            return self._refuse("open_url", why, url=url)
        if not self.vm_check():
            return self._refuse("open_url", "live UI control runs only inside the guest VM (host Mac refused)")
        if bundle not in self.allow_apps:
            return self._refuse("open_url", f"app {bundle} is not an allowed test target")
        running = self._app(bundle)
        if bundle != SAFARI:                           # e.g. TextEdit on a local file
            path = urllib.parse.unquote(urllib.parse.urlsplit(url).path) if url.startswith("file:") else url
            before = set(map(id, window_list(self.rt, running["pid"]))) if running else set()
            if not self.rt.open_with(app, path):
                return self._refuse("open_url", f"open -a {app} failed")
            info = None
            t_end = self.rt.now() + timeout
            while info is None and self.rt.now() < t_end:
                info = self._app(bundle)
                if info is None:
                    self.rt.sleep(self.poll)
            if info is None:
                return self._done(ActionResult(Outcome.UNVERIFIED, "open_url", f"{app} did not start", "open"))
            w = self._wait_window(info["pid"], lambda w: id(w) not in before, timeout)
            out = Outcome.VERIFIED if w is not None else Outcome.UNVERIFIED
            snap = snapshot_element(self.rt, w, app=info) if w is not None else None
            return self._done(ActionResult(out, "open_url", "" if w else "no new window", "open",
                                           {"url": url}, snap, w))
        if running is None:
            if not self.rt.open_with(app, url):
                return self._refuse("open_url", "open -a Safari failed")
            info = None
            t_end = self.rt.now() + timeout
            while info is None and self.rt.now() < t_end:
                info = self._app(bundle)
                if info is None:
                    self.rt.sleep(self.poll)
            if info is None:
                return self._done(ActionResult(Outcome.UNVERIFIED, "open_url", "Safari did not start", "open"))
            host = urllib.parse.urlsplit(url).netloc.lower()
            w = self._wait_window(info["pid"], lambda w: urllib.parse.urlsplit(
                self._web_url(w, info) or "").netloc.lower() == host, timeout)
            if w is None:
                return self._done(ActionResult(Outcome.UNVERIFIED, "open_url", "page did not load", "open",
                                               {"url": url}))
            snap = snapshot_element(self.rt, w, app=info, max_nodes=self.max_nodes, timeout=self.timeout)
            return self._done(ActionResult(Outcome.VERIFIED, "open_url", "", "open", {"url": url}, snap, w))
        # Safari is running: a NEW window, then navigate only that window
        known = window_list(self.rt, running["pid"])
        is_new = lambda w: not any(self.rt.same(w, k) for k in known)  # noqa: E731
        r = self.menu_pick(running, ("File", "New Window"))
        w = self._wait_window(running["pid"], is_new, 5.0) if r.outcome is not Outcome.REFUSED else None
        if w is None and self.use_events and self.rt.can_post_events():
            self.rt.activate(running["pid"])
            self.rt.post_key(running["pid"], KEY_N, FLAG_CMD)
            w = self._wait_window(running["pid"], is_new, 5.0)
        if w is None:
            return self._refuse("open_url", "could not open a new Safari window (refusing to reuse an "
                                            "existing one)")
        nav = self.navigate(w, url, app=running, timeout=timeout)
        nav.action = "open_url"
        nav.window = w
        return nav

    def _address_field(self, snap: Snapshot) -> Optional[tuple]:
        def is_addr(n):
            if norm_role(n.get("role")) not in ("textfield", "combobox", "searchfield"):
                return False
            hint = " ".join(str(n.get(k) or "") for k in ("identifier", "title", "description", "placeholder"))
            return bool(re.search(r"address|url|search or enter|WEB_BROWSER", hint, re.I))
        hits = snap.find(is_addr)
        return hits[0] if hits else None

    def navigate(self, window: Any, url: str, *, app: Optional[Dict[str, Any]] = None,
                 timeout: float = 15.0) -> ActionResult:
        """Load url in THIS Safari window through its address field."""
        ok, why = url_allowed(url, self.allow_urls)
        if not ok:
            return self._refuse("navigate", why, url=url)
        app = app or self._app(SAFARI) or {}
        g = self._gate("navigate", "type", app)
        if g:
            return g
        snap = snapshot_element(self.rt, window, app=app, max_nodes=self.max_nodes, timeout=self.timeout)
        if norm_url(snap.url) == norm_url(url):
            return self._done(ActionResult(Outcome.VERIFIED, "navigate", "already there", "noop",
                                           {"url": url}, snap, window))
        p = self._address_field(snap)
        if p is None:
            return self._refuse("navigate", "no address field in the window")
        el = snap.element(p)
        self.raise_window(snap)
        focused = (self.rt.read(app and self.rt.app_element(app["pid"]), ("AXFocusedWindow",))
                   .get("AXFocusedWindow"))
        if focused is not None and not self.rt.same(focused, window):
            return self._refuse("navigate", "our window is not the key window (refusing to type)")
        self.rt.set_attr(el, "AXFocused", True)
        set_ok = self.rt.set_attr(el, "AXValue", url)
        if not set_ok and self.use_events and self.rt.can_post_events():
            self.rt.post_key(app["pid"], KEY_A, FLAG_CMD)
            self.rt.post_text(app["pid"], url)
        if "AXConfirm" in self.rt.actions(el):
            self.rt.perform(el, "AXConfirm")
        elif self.use_events and self.rt.can_post_events():
            self.rt.post_key(app["pid"], KEY_RETURN)
        else:
            return self._refuse("navigate", "cannot confirm the address field without synthetic keys")
        old, self.settle = self.settle, timeout
        try:
            r = self._verify("navigate", "address_field", snap, None, Expect(url_changes=True), {"url": url},
                             window)
            if r.after is not None and norm_url(r.after.url) != norm_url(url):
                r.detail["redirected_to"] = urllib.parse.urlsplit(r.after.url or "").path
            return r
        finally:
            self.settle = old
