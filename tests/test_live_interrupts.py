"""System / Safari interruptions over our window (scaffold/agent/desktop/live/interrupts.py).

Live evidence (GitHub macOS runner, dev_dashboard demo a): after the sign-in click Safari showed
its "Save Password?" sheet (Save Password / Never for This Website / Not Now) and macOS showed
"Allow "Python" to find devices on local networks?" (Don't Allow / Allow). Our window stopped
being the key window and the agent refused to type the 2FA code.

These tests put the same two dialogs over the WebKit-like fake (test_live_actions_web) right
after a click: they are dismissed with the SAFE button only, focus is re-verified, and the run
continues. An unknown dialog, a dialog offering only unsafe choices, a known button set from an
unexpected owner, and a keychain prompt all end in needs_user, and nothing unsafe is pressed.
No real UI, no model calls.
"""
import json

import pytest

import test_live_actions_web as W
import test_live_driver as L
from scaffold.agent.desktop.live import actions as A
from scaffold.agent.desktop.live import interrupts as I
from scaffold.agent.desktop.live import probe as P
from scaffold.agent.desktop.secrets import MemoryProvider, SecretBroker

El, SAFARI_PID = L.El, L.SAFARI_PID
UNC_PID, SEC_PID, ODD_PID = 800, 810, 820
SAVE_PW = ("Save Password", "Never for This Website", "Not Now")


class InterruptingSafari(L.FakeSafari):
    """FakeSafari whose first form submit triggers ``inject(self, window)``; a sheet on a window
    becomes Safari's focused window (as live: our window is then not the key window)."""

    def __init__(self, rt, inject=None):
        super().__init__(rt)
        self.inject = inject
        self.sheet = None
        self.pressed = []          # every dialog button pressed, in order
        self.submits = 0

    def _app_attr(self, n):
        if n == "AXFocusedWindow" and self.sheet is not None:
            return self.sheet
        return super()._app_attr(n)

    def submit(self, w, form, button):
        super().submit(w, form, button)
        self.submits += 1
        if self.submits == 1 and self.inject is not None:
            self.inject(self, w)


def safari_sheet(saf, w, buttons=SAVE_PW):
    sheet = El("AXSheet")
    sheet.add(El("AXStaticText", AXValue="Would you like to save this password?"))

    def close():
        if sheet in w.kids:
            w.kids.remove(sheet)
        saf.sheet = None
    for b in buttons:
        e = sheet.add(El("AXButton", AXTitle=b))
        e.acts["AXPress"] = lambda b=b: (saf.pressed.append(b), close())
    w.add(sheet)
    saf.sheet = sheet
    return sheet


def system_alert(rt, saf, *, pid=UNC_PID, bundle=I.USER_NOTIFICATION_CENTER, name="UserNotificationCenter",
                 buttons=("Don't Allow", "Allow"), subrole="AXDialog",
                 text="Allow “Python” to find devices on local networks?"):
    app = El("AXApplication", pid=pid)
    win = El("AXWindow", pid=pid, AXSubrole=subrole)
    win.add(El("AXStaticText", pid=pid, AXValue=text))
    wins = [win]
    for b in buttons:
        e = win.add(El("AXButton", pid=pid, AXTitle=b.replace("'", "’")))   # macOS uses a curly ’
        e.acts["AXPress"] = lambda b=b: (saf.pressed.append(b), wins.clear())
    app.meta["computed"] = lambda n: list(wins) if n == "AXWindows" else (
        wins[0] if wins and n in ("AXFocusedWindow", "AXMainWindow") else None)
    rt.add_app(pid, bundle, name, app)
    rt.front = pid
    return win


def both(saf, w):
    safari_sheet(saf, w)
    system_alert(saf.rt, saf)


def _world(inject=None):
    rt = W.WebKitRuntime()
    saf = InterruptingSafari(rt, inject)
    rt.saf = saf
    L.owner_window(rt, saf)
    return rt, saf


# ================================================== end to end: dev_dashboard ==

def test_dd_survives_save_password_sheet_and_local_network_alert(tmp_path):
    rt, saf = _world(both)
    lines = []
    res = P.demo_dd("a", 7, tmp_path / "dd", runtime=rt, vm_check=L.VM, out=lines.append, settle=1.0)
    assert res["pass"], (res.get("reasons"), res.get("error"), lines[-30:])
    assert saf.pressed == ["Don't Allow", "Not Now"]              # the safe buttons, nothing else
    got = [(h["rule"], h["button"], h["outcome"]) for h in res["interruptions"]]
    assert got == [("system_privacy_alert", "Don't Allow", "dismissed"),
                   ("safari_save_password", "Not Now", "dismissed")]
    assert res["interruptions"][0]["owner"] == I.USER_NOTIFICATION_CENTER
    assert res["interruptions"][1]["owner"] == A.SAFARI and res["interruptions"][1]["structure"] == "sheet"
    assert any(x.startswith("INTERRUPT safari_save_password -> Not Now") for x in res["ui_log"])
    assert rt.misdirected == 0 and rt.front == SAFARI_PID            # nothing typed into the dialogs
    log = "\n".join(lines)
    assert "interruption: safari_save_password" in log
    kc = json.loads((tmp_path / "dd/sandbox/Keychain/fakeservice.json").read_text())
    assert kc["password"] not in log and kc["password"] not in json.dumps(res["interruptions"])


def test_dd_unknown_alert_needs_user_and_nothing_clicked(tmp_path):
    rt, saf = _world(lambda s, w: system_alert(rt, s, buttons=("OK", "Cancel"), text="Something else"))
    lines = []
    res = P.demo_dd("a", 7, tmp_path / "dd", runtime=rt, vm_check=L.VM, out=lines.append, settle=1.0)
    assert not res["pass"] and "error" not in res, res.get("error")
    assert res["report_status"] == "needs_user"
    assert saf.pressed == [] and rt.misdirected == 0
    last = res["interruptions"][-1]
    assert last["outcome"] == "needs_user" and last["dialog"]["buttons"] == ["Cancel", "OK"]
    assert last["dialog"]["owner"] == I.USER_NOTIFICATION_CENTER


def test_dd_save_sheet_with_only_unsafe_choices_needs_user(tmp_path):
    rt, saf = _world(lambda s, w: safari_sheet(s, w, ("Save Password", "Never for This Website")))
    res = P.demo_dd("a", 7, tmp_path / "dd", runtime=rt, vm_check=L.VM, out=lambda s: None, settle=1.0)
    assert not res["pass"] and res["report_status"] == "needs_user"
    assert saf.pressed == [] and saf.sheet is not None              # left for the human
    assert res["interruptions"][-1]["outcome"] == "needs_user"


# ============================================================ actor units ==

@pytest.fixture
def iw(tmp_path):
    rt, saf = _world()
    srv, url = L._tiny_server(tmp_path)
    ac = L.actor(rt, allow_apps={A.SAFARI}, secrets=SecretBroker(MemoryProvider({"pw": "s3cret-value-123"})))
    r = ac.open_url(url + "/")
    assert r.outcome is A.Outcome.VERIFIED, r
    yield {"rt": rt, "saf": saf, "ac": ac, "win": r.window}
    srv.shutdown()


def _fill_email(iw):
    w = {"ac": iw["ac"], "win": iw["win"]}
    return iw["ac"].set_value(W._snap(w), W._path_of_t(w, W._field(iw["win"], "email")), "ada@example.com")


def test_save_password_sheet_dismissed_with_not_now_then_types(iw):
    safari_sheet(iw["saf"], iw["win"])
    r = _fill_email(iw)
    assert r.outcome is A.Outcome.VERIFIED, r
    assert iw["saf"].pressed == ["Not Now"] and iw["saf"].sheet is None
    assert [h["rule"] for h in iw["ac"].interruptions] == ["safari_save_password"]
    assert iw["ac"].interruptions[0]["action"] == "set_value"


def test_policy_may_choose_never_for_this_website(iw):
    iw["ac"].interrupts.never_save_for_site = True
    safari_sheet(iw["saf"], iw["win"])
    assert _fill_email(iw).ok and iw["saf"].pressed == ["Never for This Website"]


def test_safari_notification_prompt_gets_dont_allow(iw):
    safari_sheet(iw["saf"], iw["win"], ("Allow", "Don’t Allow"))
    assert _fill_email(iw).ok and iw["saf"].pressed == ["Don’t Allow"]
    assert iw["ac"].interruptions[0]["rule"] == "safari_allow_notifications"


def test_known_buttons_from_an_unexpected_owner_are_never_clicked(iw):
    system_alert(iw["rt"], iw["saf"], pid=ODD_PID, bundle="com.example.lookalike", name="Lookalike")
    r = _fill_email(iw)
    assert r.outcome is A.Outcome.REFUSED and r.reason.startswith("needs_user:"), r
    assert "com.example.lookalike" in r.reason and iw["saf"].pressed == []
    assert not iw["rt"].typed


def test_keychain_prompt_is_cancelled_and_needs_user(iw):
    system_alert(iw["rt"], iw["saf"], pid=SEC_PID, bundle=I.SECURITY_AGENT, name="SecurityAgent",
                 buttons=("Always Allow", "Deny", "Allow"), text="python wants to use your confidential "
                                                                 "information stored in your keychain.")
    r = _fill_email(iw)
    assert r.outcome is A.Outcome.REFUSED and r.reason.startswith("needs_user:")
    assert iw["saf"].pressed == ["Deny"] and not iw["rt"].typed
    assert iw["ac"].interruptions[-1]["rule"] == "keychain_prompt"
    assert iw["ac"].interruptions[-1]["outcome"] == "needs_user"


def test_press_clears_interruption_first(iw):
    system_alert(iw["rt"], iw["saf"])
    w = {"ac": iw["ac"], "win": iw["win"]}
    snap = W._snap(w)
    link = next(p for p, n in [(p, snap.node(p)) for p in snap.handles] if n and n.get("role") == "AXLink")
    r = iw["ac"].press(snap, link, A.Expect(any_change=True))
    assert r.outcome is not A.Outcome.REFUSED, r
    assert iw["saf"].pressed == ["Don't Allow"] and iw["rt"].front == SAFARI_PID


def test_no_interruption_no_scan_side_effects(iw):
    assert _fill_email(iw).ok and iw["ac"].interruptions == [] and iw["saf"].pressed == []


def test_host_never_handles_interruptions(iw):
    safari_sheet(iw["saf"], iw["win"])
    iw["ac"].vm_check = lambda: False
    r = _fill_email(iw)
    assert r.outcome is A.Outcome.REFUSED and "guest VM" in r.reason and iw["saf"].pressed == []


def test_norm_title_only_typographic():
    assert I.norm_title(" Don’t  Allow ") == "Don't Allow"
    assert I.norm_title("Not Now…") == "Not Now"
    assert I.norm_title("not now") != "Not Now"                 # exact, not fuzzy


# ======================================================= guest hygiene etc. ==

class _Defaults:
    def __init__(self, value="0"):
        self.calls, self.value = [], value

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        out = self.value if argv[:2] == ["defaults", "read"] else ""
        rc = 1 if argv[0] == "pgrep" else 0
        return type("R", (), {"returncode": rc, "stdout": out, "stderr": ""})()


def test_hygiene_turns_off_safari_password_autofill_in_both_domains():
    r = _Defaults()
    res = P.vm_hygiene(runner=r, vm_check=L.VM, out=lambda s: None, sleep=lambda s: None)
    writes = [c for c in r.calls if c[:2] == ["defaults", "write"]]
    doms = {c[2] for c in writes if c[3] == "AutoFillPasswords"}
    assert "com.apple.Safari" in doms and any("/Containers/com.apple.Safari/" in d for d in doms)
    assert all(c[4:] == ["-bool", "false"] for c in writes)
    assert "container:AutoFillPasswords" in res["prefs"]["verified"]
    assert "domain:AutoFillPasswords" in res["prefs"]["verified"]
    first_write = r.calls.index(writes[0])
    assert any(c[0] == "pgrep" for c in r.calls[:first_write])      # Safari quit before prefs change


def test_hygiene_prefs_unverified_is_reported_not_fatal():
    res = P.vm_hygiene(runner=_Defaults(value="1"), vm_check=L.VM, out=lambda s: None, sleep=lambda s: None)
    assert res["prefs"]["set"] and not res["prefs"]["verified"]


def test_hygiene_prefs_never_on_host():
    r = _Defaults()
    P.vm_hygiene(runner=r, vm_check=lambda: False, out=lambda s: None, sleep=lambda s: None)
    assert r.calls == []
    assert P.safari_prefs(runner=r, vm_check=lambda: False)["set"] == [] and r.calls == []


def test_dd_servers_bind_ipv4_loopback_only(tmp_path):
    import sys
    sys.path.insert(0, str(P.REPO / "testbeds" / "dev_dashboard"))
    import dd_seed
    import dd_server
    dd_seed.seed(tmp_path, "a", 7)
    state = tmp_path / "harness" / "dashboard_state.json"
    for host in ("127.0.0.1", "localhost"):
        s = dd_server.DashboardServers(state, host=host)
        try:
            assert s.dash.server_address[0] == "127.0.0.1" and s.desk.server_address[0] == "127.0.0.1"
        finally:
            s.dash.server_close()
            s.desk.server_close()
    with pytest.raises(ValueError):
        dd_server.DashboardServers(state, host="0.0.0.0")


def test_teach_once_servers_bind_ipv4_loopback():
    import inspect
    from testbeds.teach_once import webapps
    assert '("127.0.0.1", 0)' in inspect.getsource(webapps)
