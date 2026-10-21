"""Teach-once live demo (probe.demo_teach_t3 / driver.MacEnv) against REAL-Safari behaviour.

tests/test_live_driver.py's FakeSafari answers instantly. The first VM runs showed Safari does
not, so LiveSafari / LiveRuntime here add what was observed (or follows from it):

  * page loads land later than AXPress / AXConfirm returns (the HTTP request is made at once,
    the new page renders after ``load_lag``), and the pressed link/button takes focus at
    once, so a press "verifies" against the old page;
  * NSRunningApplication.activate and AXRaise land after ``act_lag``: AXFocusedWindow is the
    other window for a while;
  * AXValue set on a web text field reports success but reads back empty and does not reach
    the page (live log: "set_value: unverified ax_value value differs (len 0, want len 19)"),
    so the driver types with keystrokes, which go to the focused field of the KEY window only.

No real UI, no model. The teach-once program is induced from the scripted demonstration.
"""
import pytest

import test_live_driver as LD
from test_live_driver import SAFARI_PID, VM, A, D, P, owner_window


class LiveRuntime(LD.FakeRuntime):
    def __init__(self, act_lag=0.3):
        super().__init__()
        self.timers = []
        self.act_lag = act_lag
        self.safari = None

    def later(self, delay, fn):
        self.timers.append((self.t + delay, fn))

    def sleep(self, s):
        self.t += s
        due = sorted((x for x in self.timers if x[0] <= self.t), key=lambda x: x[0])
        self.timers = [x for x in self.timers if x[0] > self.t]
        for _, fn in due:
            fn()

    def activate(self, pid):
        self.later(self.act_lag, lambda: setattr(self, "front", pid))
        return True

    def can_post_events(self):
        return True

    def post_key(self, pid, keycode, flags=0):
        self.events.append(("key", pid, keycode))
        if keycode == A.KEY_A and flags & A.FLAG_CMD:
            self.safari.select_all()
        return True

    def post_text(self, pid, text):
        self.events.append(("text", pid))
        self.safari.keystrokes(text)
        return True


class LiveSafari(LD.FakeSafari):
    def __init__(self, rt, load_lag=0.6, **kw):
        super().__init__(rt, **kw)
        rt.safari = self
        self.load_lag = load_lag
        self.lost = []              # keystrokes that reached no field of ours
        self.typed_into = []        # (window title, field) that received keystrokes

    def key_window(self):
        return self.windows[-1] if self.windows and self.rt.front == SAFARI_PID else None

    def new_window(self, url=None):
        w = super().new_window(url)

        def raise_later(w=w):
            def go():
                if w in self.windows:
                    self.windows.remove(w)
                    self.windows.append(w)
            self.rt.later(self.rt.act_lag, go)
        w.acts["AXRaise"] = raise_later
        return w

    def load(self, w, url, data=None):
        assert url.startswith(("http://127.0.0.1", "http://localhost")), url
        self.loads.append(url)
        try:
            r = self.op.open(url, data=data, timeout=10)
            raw, final = r.read(), r.geturl()
        except LD.urllib.error.HTTPError as e:
            raw, final = e.read(), url
        self.rt.later(self.load_lag, lambda: self._render(w, final, raw.decode("utf-8", "replace")))

    def _render(self, w, url, raw):
        super()._render(w, url, raw)
        w.meta["focus"] = None
        for el in self.walk(w.meta["web"]):
            role = el.attrs["AXRole"]
            if role == "AXTextField" and not el.meta.get("secure"):
                def on_set(name, v, el=el, w=w):
                    if name == "AXValue":                  # accepted, not applied, reads back the DOM value
                        el.attrs["AXValue"] = el.meta["real"]
                    elif name == "AXFocused" and v:
                        w.meta["focus"] = el
                el.on_set = on_set
            if role in ("AXLink", "AXButton") and "AXPress" in el.acts:
                orig = el.acts["AXPress"]

                def press(el=el, orig=orig):
                    el.attrs["AXFocused"] = True           # WebKit focuses the clicked control at once
                    orig()
                el.acts["AXPress"] = press

    def select_all(self):
        w = self.key_window()
        if w is not None:
            w.meta["selall"] = True

    def keystrokes(self, text):
        w = self.key_window()
        el = w.meta.get("focus") if w is not None else None
        if el is None:
            self.lost.append(len(text))
            return
        real = text if w.meta.pop("selall", False) else el.meta["real"] + text
        el.meta["real"] = real
        el.attrs["AXValue"] = real
        self.typed_into.append((w.attrs.get("AXTitle"), el.meta.get("field")))


def live_world(act_lag=0.3, load_lag=0.6):
    rt = LiveRuntime(act_lag=act_lag)
    saf = LiveSafari(rt, load_lag=load_lag)
    owner = owner_window(rt, saf)
    return rt, saf, owner


# ======================================================= end to end on lag ==

def test_demo_teach_t3_on_real_safari_timing(tmp_path):
    """Two windows (inbox + accounting), async loads and raises, AXValue ignored on web fields."""
    rt, saf, owner = live_world()
    lines = []
    res = P.demo_teach_t3("i03", tmp_path / "t3", runtime=rt, vm_check=VM, out=lines.append, settle=1.0)
    assert res["pass"], (res.get("status"), res.get("halt"), res.get("halt_message"), res.get("error"),
                         lines[-25:])
    assert res["status"] == "VERIFIED" and res["model_calls"] == 0
    assert not saf.lost                                  # no keystroke went to the wrong window / nowhere
    assert saf.typed_into and all(t[1] for t in saf.typed_into)
    assert any("keystrokes" in ln for ln in lines)       # the AXValue-reads-empty fallback was used
    assert saf.windows == [owner] and res["windows_closed"] == 2
    assert all("owner.example" not in u for u in saf.loads)


def test_demo_teach_t3_keep_window(tmp_path):
    rt, saf, owner = live_world(act_lag=0.0, load_lag=0.2)
    res = P.demo_teach_t3("i03", tmp_path / "t3", runtime=rt, vm_check=VM, out=lambda s: None, settle=1.0,
                          keep_window=True)
    assert res["pass"] and len(saf.windows) == 3 and "windows_closed" not in res


# ============================================================= MacEnv units ==

class _Base:
    home, context = "/nonexistent", {}

    def actions(self):
        return 0

    def secret(self, name):
        return None


def _env(rt, url, **kw):
    ac = LD.actor(rt, allow_apps={A.SAFARI})
    return D.MacEnv(_Base(), {"site": url}, actor=ac, **kw)


def test_click_waits_for_the_new_page(tmp_path):
    rt, saf, _ = live_world(act_lag=0.0, load_lag=1.2)
    srv, url = LD._tiny_server(tmp_path)
    try:
        env = _env(rt, url)
        p = env.open("site", "/")
        assert p.title == "Home"
        link = p.find("link", "Next page")
        p2 = env.click("site", link)
        assert p2 is not None and p2.title == "Two" and p2.path == "/page2"
        assert not env.notes
    finally:
        srv.shutdown()


def test_submit_after_typing_reads_the_answer_page(tmp_path):
    """The cached reading predates typing; the click must not settle on the typed-into old page."""
    rt, saf, _ = live_world(act_lag=0.2, load_lag=0.8)
    srv, url = LD._tiny_server(tmp_path)
    try:
        env = _env(rt, url)
        p = env.open("site", "/")
        email = p.find("textbox", "Email")
        env.type("site", email, "ada@example.com")
        p2 = env.click("site", env.page("site").find("button", "Sign in"))
        assert srv.got["form"]["email"] == ["ada@example.com"]
        assert p2.title == "Two"
        assert ("text", SAFARI_PID) in rt.events and not saf.lost    # AXValue was ignored -> keystrokes
        assert any(h["method"] == "keystrokes" and h["outcome"] == "verified" for h in env.actor.history)
    finally:
        srv.shutdown()


def test_switch_window_waits_until_key_then_navigates(tmp_path):
    rt, saf, owner = live_world(act_lag=0.5, load_lag=0.3)
    srv, url = LD._tiny_server(tmp_path)
    try:
        env = D.MacEnv(_Base(), {"a": url, "b": url}, actor=LD.actor(rt, allow_apps={A.SAFARI}))
        env.open("a", "/")
        env.open("b", "/")                       # b's window is now on top
        p = env.open("a", "/page2")              # navigate refuses unless a's window is key
        assert p.title == "Two" and saf.key_window() is env.windows["a"]
    finally:
        srv.shutdown()


def test_type_refuses_when_window_never_becomes_key(tmp_path):
    rt, saf, owner = live_world(act_lag=0.0, load_lag=0.2)
    srv, url = LD._tiny_server(tmp_path)
    try:
        env = _env(rt, url, focus_timeout=1.0)
        p = env.open("site", "/")
        mine = env.windows["site"]
        mine.acts["AXRaise"] = lambda: None      # e.g. a modal / another app keeps the focus
        saf.windows.remove(owner)
        saf.windows.append(owner)                # the owner's window is key
        n_text = sum(1 for e in rt.events if e[0] == "text")
        with pytest.raises(RuntimeError, match="not the key window"):
            env.type("site", p.find("textbox", "Email"), "ada@example.com")
        assert sum(1 for e in rt.events if e[0] == "text") == n_text and not saf.lost
    finally:
        srv.shutdown()


def test_window_closed_underneath_is_replaced_and_close_is_ours_only(tmp_path):
    rt, saf, owner = live_world(act_lag=0.0, load_lag=0.2)
    srv, url = LD._tiny_server(tmp_path)
    try:
        env = _env(rt, url)
        env.open("site", "/")
        gone = env.windows["site"]
        saf.windows.remove(gone)                 # someone closed it
        p = env.switch("site")
        assert p is not None and p.title == "Home" and env.windows["site"] is not gone
        assert any("went away" in n for n in env.notes) or env.windows["site"] in saf.windows
        assert env.close() == 1 and saf.windows == [owner] and not env.windows
    finally:
        srv.shutdown()


# ============================================================ guest hygiene ==

class _Runner:
    def __init__(self, safari_polls=1, fail=()):
        self.calls = []
        self.safari_polls = safari_polls       # pgrep answers "running" this many times
        self.fail = fail

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        rc, err = 0, ""
        if argv[0] == "pgrep":
            rc = 0 if self.safari_polls > 0 else 1
            self.safari_polls -= 1
        elif argv[0] == "osascript" and any(f in argv[-1] for f in self.fail):
            rc, err = 1, "Not authorized to send Apple events"
        return type("R", (), {"returncode": rc, "stdout": "", "stderr": err})()


def test_vm_hygiene_quits_safari_closes_finder_and_settings():
    r = _Runner(safari_polls=2)
    res = P.vm_hygiene(runner=r, vm_check=VM, out=lambda s: None, sleep=lambda s: None)
    scripts = [c[-1] for c in r.calls if c[0] == "osascript"]
    assert any('"Safari"' in s and "close every window" in s and "quit" in s for s in scripts)
    assert any('"Finder"' in s and "close every window" in s for s in scripts)
    assert any('"System Settings"' in s and "quit" in s for s in scripts)
    assert res["safari_quit"] and res["ran"] == ["safari", "finder", "settings"] and not res["errors"]
    assert not any(c[0] == "killall" for c in r.calls)


def test_vm_hygiene_killall_fallback_and_errors_not_fatal():
    r = _Runner(safari_polls=10 ** 6, fail=("Finder",))
    res = P.vm_hygiene(runner=r, vm_check=VM, out=lambda s: None, sleep=lambda s: None, wait=1.0)
    assert ["killall", "Safari"] in r.calls and not res["safari_quit"]
    assert any(e.startswith("finder:") for e in res["errors"])


def test_vm_hygiene_never_runs_on_host():
    r = _Runner()
    res = P.vm_hygiene(runner=r, vm_check=lambda: False, out=lambda s: None, sleep=lambda s: None)
    assert r.calls == [] and res["errors"]


def test_demo_runs_hygiene_before_any_ui_and_saves_it(tmp_path):
    rt, saf, owner = live_world(act_lag=0.0, load_lag=0.2)
    seen_loads = []

    class R(_Runner):
        def __call__(self, argv, **kw):
            seen_loads.append(len(saf.loads))
            return super().__call__(argv, **kw)
    r = R(safari_polls=0)
    out = []
    rc = P.demo("teach_t3", runtime=rt, vm_check=VM, out=out.append, root=str(tmp_path / "t3"),
                out_dir=str(tmp_path / "res"), hygiene=True, runner=r)
    assert rc == 0, out[-20:]
    assert r.calls and set(seen_loads) == {0}            # all hygiene ran before Safari loaded anything
    saved = list((tmp_path / "res").glob("teach_t3-*.json"))
    assert saved and '"hygiene"' in saved[0].read_text()
    assert saf.windows == [owner]                         # the demo's windows closed afterwards


def test_demo_hygiene_off_by_default_on_fake_runtime(tmp_path):
    rt, saf, owner = live_world(act_lag=0.0, load_lag=0.2)
    r = _Runner()
    rc = P.demo("teach_t3", runtime=rt, vm_check=VM, out=lambda s: None, root=str(tmp_path / "t3"),
                out_dir=str(tmp_path / "res"), runner=r)
    assert rc == 0 and r.calls == []


def test_demo_refuses_on_host_before_hygiene():
    rt, saf, owner = live_world()
    r = _Runner()
    assert P.demo("teach_t3", runtime=rt, vm_check=lambda: False, out=lambda s: None, hygiene=True,
                  runner=r) == 2
    assert r.calls == [] and not saf.loads
