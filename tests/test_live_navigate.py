"""navigate(): the address field must hold exactly the requested URL before it is confirmed.

Live evidence (2026-10-21, sandbox VM, Safari history): two local-model dd runs halted
"could not read the key list ... http 404" because Safari loaded
``http://127.0.0.1:P/projects/storefront-web/keyshttp://127.0.0.1:P/projects/storefront-web/keys``:
AXValue on the address field reported failure although it WAS applied, Cmd-A did not
select, and the keystroke fallback appended a second copy.
"""
from scaffold.agent.desktop.live import actions as A

from tests.test_live_driver import FakeRuntime, FakeSafari, _tiny_server, actor


class EventRuntime(FakeRuntime):
    """Synthetic events work; typed text goes into the focused address field."""

    def __init__(self, saf_box, *, set_lies=True, cmd_a_selects=False, typing_works=True):
        super().__init__()
        self.box, self.set_lies, self.cmd_a, self.typing = saf_box, set_lies, cmd_a_selects, typing_works
        self.selected = False

    def _addr(self):
        return self.box[0].windows[-1].meta["addr"]

    def set_attr(self, el, name, value):
        ok = super().set_attr(el, name, value)
        if name == "AXValue" and self.set_lies and el is self._addr():
            return False                     # applied, but reports failure (live Safari)
        return ok

    def can_post_events(self):
        return True

    def post_key(self, pid, keycode, flags=0):
        self.events.append(("key", pid, keycode))
        if keycode == A.KEY_A and flags & A.FLAG_CMD and self.cmd_a:
            self.selected = True
        if keycode == A.KEY_RETURN:
            a = self._addr()
            a.acts["AXConfirm"]()
        return True

    def post_text(self, pid, text):
        self.events.append(("text", pid))
        if self.typing:
            a = self._addr()
            a.attrs["AXValue"] = text if self.selected else (a.attrs.get("AXValue") or "") + text
            self.selected = False
        return True


def _run(tmp_path, **kw):
    box = []
    rt = EventRuntime(box, **kw)
    saf = FakeSafari(rt)
    box.append(saf)
    srv, url = _tiny_server(tmp_path)
    try:
        ac = actor(rt)
        r = ac.open_url(url + "/")
        assert r.outcome is A.Outcome.VERIFIED
        r2 = ac.navigate(r.window, url + "/page2")
        return r2, saf, url
    finally:
        srv.shutdown()


def test_lying_setter_does_not_double_the_url(tmp_path):
    r, saf, url = _run(tmp_path)
    assert r.outcome is A.Outcome.VERIFIED, r
    assert saf.loads[-1] == url + "/page2"
    assert all(u.count("http") == 1 for u in saf.loads)


def test_mangled_field_is_repaired_or_refused_never_loaded(tmp_path):
    # setter fails for real, Cmd-A does not select, typing appends to the old URL
    box = []
    rt = EventRuntime(box, set_lies=False, cmd_a_selects=False)
    saf = FakeSafari(rt)
    box.append(saf)
    srv, url = _tiny_server(tmp_path)
    try:
        ac = actor(rt)
        w = ac.open_url(url + "/").window
        addr = w.meta["addr"]
        addr.settable.discard("AXValue")                     # AXValue not settable at all
        r = ac.navigate(w, url + "/page2")
        assert r.outcome is A.Outcome.REFUSED and "address field" in r.reason
        assert all(u.count("http") == 1 for u in saf.loads)
    finally:
        srv.shutdown()


def test_typing_path_still_works_when_select_all_works(tmp_path):
    box = []
    rt = EventRuntime(box, set_lies=False, cmd_a_selects=True)
    saf = FakeSafari(rt)
    box.append(saf)
    srv, url = _tiny_server(tmp_path)
    try:
        ac = actor(rt)
        w = ac.open_url(url + "/").window
        w.meta["addr"].settable.discard("AXValue")
        r = ac.navigate(w, url + "/page2")
        assert r.outcome is A.Outcome.VERIFIED, r
        assert saf.loads[-1] == url + "/page2"
    finally:
        srv.shutdown()


def test_cold_safari_launch_waits_past_15s(tmp_path):
    """Live: a cold Safari exposes the new window's web URL only ~10s+ after `open`."""
    from tests.test_live_driver import SAFARI_PID
    rt = FakeRuntime()
    saf = FakeSafari(rt, running=False)
    srv, url = _tiny_server(tmp_path)
    state = {}

    def open_with(app, target):
        rt.add_app(SAFARI_PID, A.SAFARI, "Safari", saf.app)
        state["at"], state["url"] = rt.now() + 20.0, target       # page shows up 20s later
        return True

    real_read = rt.read

    def read(el, names):
        if "at" in state and rt.now() >= state["at"] and not saf.windows:
            saf.new_window(state["url"])
        return real_read(el, names)
    rt.open_with, rt.read = open_with, read
    try:
        r = actor(rt).open_url(url + "/")
        assert r.outcome is A.Outcome.VERIFIED, r
    finally:
        srv.shutdown()
