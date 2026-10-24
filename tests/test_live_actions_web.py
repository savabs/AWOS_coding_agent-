"""Issue C: text entry into REAL Safari web fields (scaffold/agent/desktop/live/actions.py).

Live evidence (first VM runs, demo_dd*.log): every create-key fill logged
    UI set_value: unverified ax_value value differs (len 0, want len 19)
    UI set_value: verified keystrokes
i.e. WebKit answers AXUIElementSetAttributeValue(AXValue) with success on a web text field but
the value reads back empty, so each fill cost a failed AXValue round (a full settle timeout)
before keystrokes worked. Once a field could not be focused because a window covered it.

WebKitRuntime below models exactly that on top of the fake AX runtime of test_live_driver
(which renders the testbeds' real HTML): AXValue on a non-secure web field reports success
and does nothing, CGEvent keystrokes go to the focused element of the app's KEY window only,
Cmd-A selects, Cmd-Right / Delete edit, and another app's window can cover the click point.
"""
import json

import pytest

import test_live_driver as L
from scaffold.agent.desktop.grounding import Target
from scaffold.agent.desktop.live import actions as A
from scaffold.agent.desktop.live import probe as P
from scaffold.agent.desktop.secrets import MemoryProvider, SecretBroker

El, SAFARI_PID, OTHER_PID = L.El, L.SAFARI_PID, L.OTHER_PID


def _window_of(el):
    while el is not None and el.attrs.get("AXRole") != "AXWindow":
        el = el.parent
    return el


def _in_web(el):
    p = el.parent
    while p is not None:
        if p.attrs.get("AXRole") == "AXWebArea":
            return True
        p = p.parent
    return False


class WebKitRuntime(L.FakeRuntime):
    """FakeRuntime + synthetic events with real-Safari web-field behaviour."""

    def __init__(self):
        super().__init__()
        self.focus = {}                 # pid -> focused El
        self.ax_value_web_attempts = 0
        self.cover = None               # an element of another app covering every click point
        self.cover_lifts_on_activate = True
        self.typed = []                 # (pid, n chars) -- never the text itself
        self.misdirected = 0
        self.saf = None

    # --- AX writes with the WebKit quirk -------------------------------------
    def set_attr(self, el, name, value):
        if name == "AXValue" and _in_web(el) and not el.meta.get("secure") and not el.meta.get("ax_value_works"):
            if name not in el.settable:
                return False
            self.ax_value_web_attempts += 1
            return True                                   # success reported, nothing stored
        if name == "AXValue" and el.meta.get("secure") and el.meta.get("secure_ax_value_broken"):
            return True
        if name == "AXFocused" and value is True:
            if name not in el.settable or el.meta.get("nofocus"):
                return name in el.settable              # reports success, focus does not move
            self._focus(el)
            return True
        return super().set_attr(el, name, value)

    def _focus(self, el):
        old = self.focus.get(el.pid)
        if old is not None and old is not el:
            old.attrs["AXFocused"] = False
        el.attrs["AXFocused"] = True
        self.focus[el.pid] = el
        n = len(self._get(el))
        el.attrs["AXSelectedTextRange"] = (n, 0)

    # --- value helpers ------------------------------------------------------------
    @staticmethod
    def _get(el):
        return el.meta.get("real", "") if el.meta.get("secure") else str(el.attrs.get("AXValue", ""))

    @staticmethod
    def _put(el, v):
        if el.meta.get("secure"):
            el.meta["real"] = v
        else:
            el.attrs["AXValue"] = v
            el.meta["real"] = v

    def _target(self, pid):
        el = self.focus.get(pid)
        if el is None:
            return None
        key = self.apps[pid][1].meta["computed"]("AXFocusedWindow") if callable(
            self.apps[pid][1].meta.get("computed")) else None
        if key is not None and _window_of(el) is not key:
            self.misdirected += 1                         # would land in another window
            return None
        return el

    # --- synthetic events -------------------------------------------------------------
    def can_post_events(self):
        return True

    def activate(self, pid):
        super().activate(pid)
        if self.cover is not None and self.cover_lifts_on_activate and pid != self.cover.pid:
            self.cover = None
        return True

    def element_at_point(self, x, y):
        if self.cover is not None:
            return self.cover
        if self.saf is None or not self.saf.windows:
            return None
        key = self.saf.windows[-1]
        for e in L.FakeSafari.walk(key):
            p, s = e.attrs.get("AXPosition"), e.attrs.get("AXSize")
            if p and s and p[0] <= x <= p[0] + s[0] and p[1] <= y <= p[1] + s[1]:
                return e
        return key

    def post_click(self, x, y):
        self.events.append(("click", x, y))
        hit = self.element_at_point(x, y)
        if hit is not None and hit.attrs.get("AXRole") == "AXTextField" and hit is not self.cover:
            self._focus(hit)
        return True

    def post_key(self, pid, keycode, flags=0):
        self.events.append(("key", pid, keycode))
        el = self._target(pid)
        if el is None:
            return True
        v = self._get(el)
        loc, ln = el.attrs.get("AXSelectedTextRange", (len(v), 0))
        if keycode == A.KEY_A and flags & A.FLAG_CMD:
            if not el.meta.get("no_select_all"):
                el.attrs["AXSelectedTextRange"] = (0, len(v))
        elif keycode == A.KEY_RIGHT and flags & A.FLAG_CMD:
            el.attrs["AXSelectedTextRange"] = (len(v), 0)
        elif keycode == A.KEY_DELETE and not el.meta.get("no_delete"):
            if ln:
                v = v[:loc] + v[loc + ln:]
            elif loc:
                v, loc = v[:loc - 1] + v[loc:], loc - 1
            self._put(el, v)
            el.attrs["AXSelectedTextRange"] = (min(loc, len(v)), 0)
        return True

    def post_text(self, pid, text):
        self.events.append(("text", pid))
        self.typed.append((pid, len(text)))
        el = self._target(pid)
        if el is None:
            return True
        v = self._get(el)
        loc, ln = el.attrs.get("AXSelectedTextRange", (len(v), 0))
        v = v[:loc] + text + v[loc + ln:]
        self._put(el, v)
        el.attrs["AXSelectedTextRange"] = (loc + len(text), 0)
        return True


# =================================================================== fixtures ==

@pytest.fixture
def world(tmp_path):
    rt = WebKitRuntime()
    saf = L.FakeSafari(rt)
    rt.saf = saf
    owner = L.owner_window(rt, saf)
    srv, url = L._tiny_server(tmp_path)
    broker = SecretBroker(MemoryProvider({"pw": "s3cret-value-123"}))
    ac = L.actor(rt, allow_apps={A.SAFARI}, secrets=broker)
    r = ac.open_url(url + "/")
    assert r.outcome is A.Outcome.VERIFIED, r
    yield {"rt": rt, "saf": saf, "owner": owner, "srv": srv, "url": url, "ac": ac, "win": r.window}
    srv.shutdown()


def _field(w, name):
    for e in L.FakeSafari.walk(w.meta["web"]):
        if e.attrs.get("AXRole") == "AXTextField" and e.meta.get("field") == name:
            return e
    raise AssertionError(name)


def _snap(world):
    return world["ac"].reobserve(world["ac"].observe("Safari", world["win"]))




# ============================================================ the live quirk ==

def test_fake_reproduces_the_webkit_ax_value_quirk(world):
    """AXValue on a web text field: success reported, value reads back empty (as live)."""
    rt, email = world["rt"], _field(world["win"], "email")
    assert rt.settable(email, "AXValue") and rt.set_attr(email, "AXValue", "storefront-web-awos")
    assert rt.read(email, ("AXValue",))["AXValue"] == ""


def test_web_field_types_up_front_no_failed_ax_value_round(world):
    ac, rt, email = world["ac"], world["rt"], _field(world["win"], "email")
    lines = []
    ac._log = lines.append
    snap = _snap(world)
    path = _path_of_t(world, email)
    assert ac.in_web_area(snap, path)
    r = ac.set_value(snap, path, "storefront-web-awos")
    assert r.outcome is A.Outcome.VERIFIED and r.method == "keystrokes", r
    assert r.detail["strategy"] == "keystrokes_first" and r.detail["focus"] == "ax_focused"
    assert r.detail["clear"] == "empty"
    assert rt.ax_value_web_attempts == 0                    # no wasted AXValue round
    assert not any("unverified" in x for x in lines)        # unlike the live log
    assert email.attrs["AXValue"] == "storefront-web-awos"
    assert rt.typed == [(SAFARI_PID, 19)] and rt.misdirected == 0
    assert world["owner"].meta["web"].attrs["AXURL"] == "https://owner.example/inbox"


def test_native_field_keeps_ax_value_first():
    rt = WebKitRuntime()
    s = L.static_page(rt)
    ac = L.actor(rt)
    r = ac.set_value(ac.observe("TextEdit"), Target(role="textfield", name="Name"), "Ada")
    assert r.outcome is A.Outcome.VERIFIED and r.method == "ax_value"
    assert r.detail["web"] is False and r.detail["strategy"] == "ax_value_first"
    assert s["name"].attrs["AXValue"] == "Ada" and not rt.events     # no keystrokes needed


def test_web_field_without_events_falls_back_to_ax_value(world):
    """No synthetic events (e.g. no posting grant): AXValue is still tried, and is enough
    when the page honours it."""
    rt, email = world["rt"], _field(world["win"], "email")
    email.meta["ax_value_works"] = True
    rt.can_post_events = lambda: False
    r = world["ac"].set_value(_snap(world), _path_of_t(world, _field(world["win"], "email")), "ada@example.com")
    assert r.outcome is A.Outcome.VERIFIED and r.method == "ax_value"
    assert r.detail["strategy"] == "ax_value_first" and not rt.typed


def test_typing_unverified_then_one_ax_value_attempt(world):
    """Typing reached nothing (e.g. an IME swallowed it); one AXValue attempt rescues the fill."""
    rt, email = world["rt"], _field(world["win"], "email")
    email.meta["ax_value_works"] = True
    rt.post_text = lambda pid, text: rt.typed.append((pid, len(text))) or True
    r = world["ac"].set_value(_snap(world), _path_of_t(world, _field(world["win"], "email")), "ada@example.com")
    assert r.outcome is A.Outcome.VERIFIED and r.method == "ax_value"
    assert r.detail["tried"].startswith("keystrokes: value differs")


# ================================================================== clearing ==

def test_existing_text_is_replaced_via_verified_select_all(world):
    rt, email = world["rt"], _field(world["win"], "email")
    email.attrs["AXValue"] = "old@example.com"
    r = world["ac"].set_value(_snap(world), _path_of_t(world, _field(world["win"], "email")), "new@example.com")
    assert r.outcome is A.Outcome.VERIFIED and r.detail["clear"] == "select_all"
    assert email.attrs["AXValue"] == "new@example.com"


def test_select_all_not_honoured_falls_back_to_delete(world):
    rt, email = world["rt"], _field(world["win"], "email")
    email.attrs["AXValue"] = "old"
    email.meta["no_select_all"] = True
    r = world["ac"].set_value(_snap(world), _path_of_t(world, _field(world["win"], "email")), "new@example.com")
    assert r.outcome is A.Outcome.VERIFIED and r.detail["clear"] == "deleted"
    assert email.attrs["AXValue"] == "new@example.com"


def test_text_that_cannot_be_cleared_is_never_appended_to(world):
    rt, email = world["rt"], _field(world["win"], "email")
    email.attrs["AXValue"] = "old"
    email.meta.update(no_select_all=True, no_delete=True)
    r = world["ac"].set_value(_snap(world), _path_of_t(world, _field(world["win"], "email")), "new@example.com")
    assert r.outcome is A.Outcome.REFUSED and "could not clear" in r.reason
    assert email.attrs["AXValue"] == "old" and not rt.typed


# ============================================================== secure fields ==

def test_secure_web_field_only_via_broker_and_ax_value_first(world):
    ac, rt, pw = world["ac"], world["rt"], _field(world["win"], "password")
    snap = _snap(world)
    r = ac.set_value(snap, _path_of_t(world, pw), "hunter2")
    assert r.outcome is A.Outcome.REFUSED and "handle" in r.reason and not rt.typed
    r = ac.set_value(_snap(world), _path_of_t(world, pw), "{{secret:pw}}")
    assert r.outcome is A.Outcome.VERIFIED and r.method == "ax_value"   # proven live on Safari
    assert pw.meta["real"] == "s3cret-value-123" and not rt.typed
    assert "s3cret" not in json.dumps(r.to_dict())


def test_secure_web_field_falls_back_to_keystrokes_without_leaking(world):
    ac, rt, pw = world["ac"], world["rt"], _field(world["win"], "password")
    pw.meta["secure_ax_value_broken"] = True
    lines = []
    ac._log = lines.append
    r = ac.set_value(_snap(world), _path_of_t(world, pw), "{{secret:pw}}")
    assert r.outcome is A.Outcome.VERIFIED and r.method == "keystrokes", r
    assert pw.meta["real"] == "s3cret-value-123" and rt.typed == [(SAFARI_PID, 16)]
    blob = json.dumps(r.to_dict()) + "\n".join(lines) + repr(ac.history)
    assert "s3cret" not in blob and r.detail["value"] == "{{secret:pw}}"


def _path_of_t(world, el):
    snap = _snap(world)
    for p, h in snap.handles.items():
        if h is el:
            return p
    raise AssertionError("no path")


# ================================================================== occlusion ==

def _unfocusable_with_frame(world):
    email = _field(world["win"], "email")
    email.meta["nofocus"] = True                       # AXFocused "succeeds" but focus stays put
    email.attrs.update(AXPosition=(100.0, 200.0), AXSize=(300.0, 24.0))
    return email


def test_occluded_field_raises_window_and_retries_once(world):
    rt = world["rt"]
    email = _unfocusable_with_frame(world)
    rt.cover = El("AXWindow", pid=OTHER_PID, AXTitle="Notification")
    raised = []
    orig = world["win"].acts["AXRaise"]
    world["win"].acts["AXRaise"] = lambda: (raised.append(1), orig())
    r = world["ac"].set_value(_snap(world), _path_of_t(world, _field(world["win"], "email")), "ada@example.com")
    assert r.outcome is A.Outcome.VERIFIED and r.detail["focus"] == "cg_click_after_raise", r
    assert raised and rt.front == SAFARI_PID and email.attrs["AXValue"] == "ada@example.com"


def test_genuinely_occluded_field_is_refused_and_nothing_typed(world):
    rt = world["rt"]
    email = _unfocusable_with_frame(world)
    rt.cover = El("AXWindow", pid=OTHER_PID, AXTitle="Always on top")
    rt.cover_lifts_on_activate = False
    r = world["ac"].set_value(_snap(world), _path_of_t(world, _field(world["win"], "email")), "ada@example.com")
    assert r.outcome is A.Outcome.REFUSED and "occluded" in r.reason
    assert not rt.typed and email.attrs["AXValue"] == ""
    assert sum(1 for e in rt.events if e[0] == "click") == 0


def test_point_in_another_window_of_the_same_app_is_refused(world):
    rt = world["rt"]
    _unfocusable_with_frame(world)
    other = El("AXTextField", pid=SAFARI_PID, AXWindow=world["owner"])
    rt.cover, rt.cover_lifts_on_activate = other, False
    r = world["ac"].set_value(_snap(world), _path_of_t(world, _field(world["win"], "email")), "ada@example.com")
    assert r.outcome is A.Outcome.REFUSED and "another window" in r.reason
    assert not rt.typed and not any(e[0] == "click" for e in rt.events)


def test_never_types_when_our_window_cannot_become_key(world):
    saf, rt, win, owner = world["saf"], world["rt"], world["win"], world["owner"]
    saf.windows.remove(owner)
    saf.windows.append(owner)                           # the owner's window is key now
    win.acts["AXRaise"] = lambda: None                  # and ours refuses to come forward
    r = world["ac"].set_value(_snap(world), _path_of_t(world, _field(world["win"], "email")), "ada@example.com")
    assert r.outcome is A.Outcome.REFUSED and "key window" in r.reason
    assert not rt.typed and rt.misdirected == 0


def test_type_text_uses_same_focus_and_clear_path(world):
    email = _field(world["win"], "email")
    email.attrs["AXValue"] = "x"
    r = world["ac"].type_text(_snap(world), _path_of_t(world, _field(world["win"], "email")), "typed@example.com")
    assert r.outcome is A.Outcome.VERIFIED and r.action == "type" and r.detail["clear"] == "select_all"
    assert email.attrs["AXValue"] == "typed@example.com"


# ============================================== end to end on the testbed ==

def test_demo_dd_on_webkit_fake_fills_by_keystrokes_without_failed_rounds(tmp_path):
    rt = WebKitRuntime()
    saf = L.FakeSafari(rt)
    rt.saf = saf
    L.owner_window(rt, saf)
    lines = []
    res = P.demo_dd("a", 7, tmp_path / "dd", runtime=rt, vm_check=L.VM, out=lines.append, settle=1.0)
    assert res["pass"], (res.get("reasons"), res.get("error"), lines[-30:])
    log = "\n".join(lines)
    assert "unverified ax_value" not in log and "verified keystrokes" in log
    assert rt.ax_value_web_attempts == 0 and rt.misdirected == 0
    kc = json.loads((tmp_path / "dd/sandbox/Keychain/fakeservice.json").read_text())
    assert kc["password"] not in log
