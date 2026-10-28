"""Human checkpoint + read-only pack actor on the FAKE AX runtime
(scaffold/agent/desktop/live/checkpoint.py, pack/policy.py, the actions.url_allowed hook).

A fake "real portal" Safari (test_live_driver's FakeSafari, pages served from memory instead of
127.0.0.1) shows https pages of a source: an invoices page, a login page, an OTP prompt, a
captcha, a session-expired page, an unusual-activity warning, and a redirect off the
allowlist. A scheduled "owner" changes the page while the checkpoint polls on the fake clock.
No real UI, no network, no model calls; notifications go to a recorder.
"""
import pytest

import test_live_driver as L
from scaffold.agent.desktop.grounding import Target
from scaffold.agent.desktop.live import actions as A
from scaffold.agent.desktop.live import checkpoint as C
from scaffold.agent.desktop.live import observe as O
from scaffold.agent.desktop.pack import policy as P
from scaffold.agent.desktop.secrets import MemoryProvider, SecretBroker

El = L.El
SRC = P.SourceConfig("Stripe", domains=("stripe.com",), sso_domains=("accounts.google.com",))
BASE = "https://dashboard.stripe.com"

INVOICES = """<html><head><title>Invoices</title></head><body>
<h1>Invoices</h1>
<div role="region" aria-label="Cookie consent"><button>Accept all</button><button>Reject all</button></div>
<form action="/invoices" method="get"><label for="from">From date</label><input id="from" name="from" value="">
<button type="submit">Apply</button></form>
<a href="/invoices/in_1">View invoice INV-001</a>
<a href="/statements/2026-09.pdf">Download statement</a>
<form action="/pay" method="post"><button type="submit">Pay now</button></form>
<form action="/settings/plan" method="post"><button type="submit">Upgrade</button></form>
<button>Remove card</button>
<a href="https://evil.example/help">Help</a>
<a href="/logout">Sign out</a>
</body></html>"""
STATEMENT = "<html><head><title>Statement</title></head><body><h1>Statement September</h1><a href='/invoices'>Back</a></body></html>"
LOGIN = """<html><head><title>Sign in - Stripe</title></head><body><h1>Sign in to your account</h1>
<form action="/login" method="post"><label for="e">Email</label><input id="e" name="email">
<label for="p">Password</label><input id="p" name="password" type="password">
<button type="submit">Sign in</button></form></body></html>"""
LOGIN_USERNAME_FIRST = """<html><head><title>Stripe</title></head><body><h2>Log in</h2>
<label for="e">Email</label><input id="e" name="email"><button>Next</button></body></html>"""
OTP = """<html><head><title>Verify</title></head><body><h1>Two-step verification</h1>
<p>Enter the 6-digit code we sent to your phone.</p>
<label for="c">Verification code</label><input id="c" name="code"><button>Verify</button></body></html>"""
OTP_BANK = """<html><head><title>NetBanking</title></head><body><p>An OTP has been sent to your registered mobile.</p>
<input id="o" name="o" aria-label="Enter value"><button>Submit</button></body></html>"""
CAPTCHA = """<html><head><title>Check</title></head><body><h1>One more step</h1>
<div role="region" aria-label="reCAPTCHA"><button aria-label="I'm not a robot">verify</button></div></body></html>"""
CAPTCHA_TEXT = """<html><head><title>Login</title></head><body><p>Type the characters in the image (captcha)</p>
<input aria-label="Characters"></body></html>"""
EXPIRED = "<html><head><title>Stripe</title></head><body><h1>Your session has expired</h1><a href='/login'>Log in</a></body></html>"
UNUSUAL = "<html><head><title>Stripe</title></head><body><h1>We noticed unusual activity on your account</h1></body></html>"


class Human(L.FakeRuntime):
    """FakeRuntime whose clock can fire scheduled 'owner' actions."""

    def __init__(self):
        super().__init__()
        self.timers = []

    def at(self, t, fn):
        self.timers.append((t, fn))

    def sleep(self, s):
        super().sleep(s)
        due = [x for x in self.timers if x[0] <= self.t]
        self.timers = [x for x in self.timers if x[0] > self.t]
        for _, fn in due:
            fn()


class PortalSafari(L.FakeSafari):
    """FakeSafari serving https 'portal' pages from memory (no network)."""

    def __init__(self, rt, pages, redirects=None):
        super().__init__(rt)
        self.pages = dict(pages)
        self.redirects = dict(redirects or {})
        rt.saf = self

    def load(self, w, url, data=None):
        self.loads.append(url)
        for _ in range(5):
            if url in self.redirects:
                url = self.redirects[url]
        if data is not None:
            self.posts.append((url, data))
        html = self.pages.get(url.split("?")[0], "<html><head><title>404</title></head><body><h1>Not found</h1></body></html>")
        self._render(w, url, html)

    def _render(self, w, url, raw):
        super()._render(w, url, raw)
        for e in L.FakeSafari.walk(w):                 # on screen, so grounding accepts Targets
            e.attrs.setdefault("AXPosition", (0, 0))
            e.attrs.setdefault("AXSize", (1000, 1000))

    def show(self, w, url, html):
        """The owner did something in the window: it now shows this page."""
        self._render(w, url, html)


def setup(pages, redirects=None, **kw):
    rt = Human()
    saf = PortalSafari(rt, pages, redirects)
    w = saf.new_window()
    notes, digest = [], []
    actor = A.Actor(rt, vm_check=lambda: True, settle=1.0, approve=kw.pop("approve", None),
                    secrets=kw.pop("secrets", None))
    pa = C.PackActor(actor, SRC, notify=lambda t, m: notes.append((t, m)), digest=digest.append,
                     timeout_s=kw.pop("timeout_s", 600.0), **kw)
    return rt, saf, w, pa, notes, digest


def snap_of(rt, w):
    return O.snapshot_element(rt, w, app=rt.apps[L.SAFARI_PID][0])


def show(rt, saf, w, url, html):
    saf.show(w, url, html)
    return snap_of(rt, w)


# ------------------------------------------------------------------ detection --
@pytest.mark.parametrize("html,kind", [
    (LOGIN, "login"), (LOGIN_USERNAME_FIRST, "login"), (OTP, "otp"), (OTP_BANK, "otp"), (CAPTCHA, "captcha"),
    (CAPTCHA_TEXT, "captcha"), (EXPIRED, "session_expired"), (UNUSUAL, "unusual_activity"),
])
def test_detects_human_pages(html, kind):
    rt, saf, w, pa, *_ = setup({})
    snap = show(rt, saf, w, BASE + "/x", html)
    det = C.detect(snap, SRC)
    assert det is not None and det.kind == kind, det
    with pytest.raises(C.HumanNeeded) as e:
        pa.checkpoint.check(snap)
    assert e.value.kind == kind and e.value.source == "Stripe" and e.value.url_host == "dashboard.stripe.com"


def test_normal_pages_are_clear_and_safari_chrome_is_ignored():
    rt, saf, w, pa, *_ = setup({})
    assert C.detect(show(rt, saf, w, BASE + "/invoices", INVOICES), SRC) is None
    # the address field holding a /login URL is browser chrome, not the page
    assert C.detect(show(rt, saf, w, BASE + "/login-history", STATEMENT), SRC) is None


def test_foreign_domain_detected():
    rt, saf, w, pa, *_ = setup({})
    det = C.detect(show(rt, saf, w, "https://evil.example/x", STATEMENT), SRC)
    assert det.kind == "foreign_domain"


# ------------------------------------------------------------------ allowlist --
def test_plain_actor_still_refuses_real_sites_and_hook_allows_source():
    rt = Human()
    PortalSafari(rt, {BASE + "/invoices": INVOICES})
    bare = A.Actor(rt, vm_check=lambda: True, settle=1.0)
    r = bare.open_url(BASE + "/invoices")
    assert r.outcome is A.Outcome.REFUSED and "127.0.0.1" in r.reason


def test_navigate_on_allowlist_then_redirect_to_foreign_domain_stops():
    pages = {BASE + "/invoices": INVOICES, "https://evil.example/login": LOGIN}
    rt, saf, w, pa, notes, digest = setup(pages, {BASE + "/billing": "https://evil.example/login"})
    r = pa.navigate(w, BASE + "/invoices", app=rt.apps[L.SAFARI_PID][0])
    assert r.outcome is A.Outcome.VERIFIED and r.after.url == BASE + "/invoices"
    t0 = rt.t
    r = pa.navigate(w, BASE + "/billing", app=rt.apps[L.SAFARI_PID][0])
    assert r.outcome is A.Outcome.REFUSED and r.reason.startswith(A.NEEDS_USER)
    assert r.detail["needs_you"]["kind"] == "foreign_domain"
    assert r.detail["needs_you"]["url_host"] == "evil.example"
    assert notes and "evil.example" in notes[-1][1] and "Stripe" in notes[-1][1]
    assert any("needs_you: foreign_domain" in d for d in digest)
    assert rt.t - t0 < 60                              # a stop, not a 10-minute wait
    # never going there directly either
    r = pa.navigate(w, "https://evil.example/login", app=rt.apps[L.SAFARI_PID][0])
    assert r.outcome is A.Outcome.REFUSED and "allowlist" in r.reason
    assert saf.loads.count("https://evil.example/login") == 0


def test_open_url_new_window_on_source_domain():
    rt, saf, w, pa, *_ = setup({BASE + "/invoices": INVOICES})
    r = pa.open_url(BASE + "/invoices")
    assert r.outcome is A.Outcome.VERIFIED and r.after.url == BASE + "/invoices"
    assert pa.open_url("http://dashboard.stripe.com/invoices").outcome is A.Outcome.REFUSED


# ------------------------------------------------------------------ read-only --
@pytest.mark.parametrize("name", ["Pay now", "Upgrade", "Remove card", "Sign out", "Accept all"])
def test_deny_list_refused_even_with_approval(name):
    rt, saf, w, pa, notes, _ = setup({}, approve=lambda req: True)      # an approve that says yes to anything
    snap = show(rt, saf, w, BASE + "/invoices", INVOICES)
    n_loads = len(saf.loads)
    r = pa.press(snap, Target(name=name))
    assert r.outcome is A.Outcome.REFUSED and "read-only pack" in r.reason, r.reason
    assert len(saf.loads) == n_loads and not saf.posts
    assert not notes


def test_foreign_link_refused_and_reads_allowed():
    pages = {BASE + "/statements/2026-09.pdf": STATEMENT, BASE + "/invoices": INVOICES}
    rt, saf, w, pa, *_ = setup(pages)
    snap = show(rt, saf, w, BASE + "/invoices", INVOICES)
    assert pa.press(snap, Target(role="link", name="Help")).outcome is A.Outcome.REFUSED
    r = pa.press(snap, Target(role="link", name="Download statement"))
    assert r.outcome is A.Outcome.VERIFIED and r.after.url.endswith("2026-09.pdf")
    snap = show(rt, saf, w, BASE + "/invoices", INVOICES)
    r = pa.press(snap, Target(name="Reject all"))                       # minimal cookie choice
    assert r.outcome is not A.Outcome.REFUSED, r.reason


def test_menu_pick_and_select_respect_deny_list():
    rt, saf, w, pa, *_ = setup({})
    r = pa.menu_pick(rt.apps[L.SAFARI_PID][0], ("File", "Delete"))
    assert r.outcome is A.Outcome.REFUSED and "read-only" in r.reason


# ------------------------------------------------------------- secure fields --
def test_never_types_into_password_otp_or_captcha_fields():
    broker = SecretBroker(MemoryProvider({"pw": "hunter2hunter2"}))
    rt, saf, w, pa, *_ = setup({}, secrets=broker)
    snap = show(rt, saf, w, BASE + "/login", LOGIN)
    pw = Target(role="textfield", name="Password")
    for v in ("{{secret:pw}}", A.Revealed("hunter2hunter2"), "plain"):
        r = pa.set_value(snap, pw, v)
        assert r.outcome is A.Outcome.REFUSED
    assert pa.type_text(snap, pw, "x").outcome is A.Outcome.REFUSED
    assert pa.set_value(snap, Target(role="textfield", name="Email"), "me@x.in").outcome is A.Outcome.REFUSED
    pw_el = next(e for e in L.FakeSafari.walk(w) if e.meta.get("secure"))
    assert pw_el.meta["real"] == ""
    assert not [e for e in rt.events if e[0] == "text"]
    snap = show(rt, saf, w, BASE + "/verify", OTP)
    assert pa.set_value(snap, Target(role="textfield", name="Verification code"), "123456").outcome \
        is A.Outcome.REFUSED
    snap = show(rt, saf, w, BASE + "/check", CAPTCHA)
    r = pa.press(snap, Target(role="button", name="I'm not a robot"))
    assert r.outcome is A.Outcome.REFUSED and "captcha" in r.reason
    assert rt.t < 30                    # refused at once, not after waiting out the checkpoint


def test_types_into_date_filter():
    rt, saf, w, pa, *_ = setup({})
    snap = show(rt, saf, w, BASE + "/invoices", INVOICES)
    r = pa.set_value(snap, Target(role="textfield", name="From date"), "01/09/2026")
    assert r.outcome is A.Outcome.VERIFIED, r.reason


# ---------------------------------------------------- checkpoint: wait/resume --
def test_login_page_after_navigation_waits_then_resumes():
    pages = {BASE + "/invoices": INVOICES, BASE + "/login": LOGIN}
    rt, saf, w, pa, notes, digest = setup(pages, {BASE + "/invoices": BASE + "/login"})
    reads = []
    real = pa.actor.reobserve
    pa.actor.reobserve = lambda s: reads.append(rt.t) or real(s)
    t0 = rt.t
    rt.at(t0 + 42, lambda: saf.show(w, BASE + "/invoices", INVOICES))         # the owner logs in
    r = pa.navigate(w, BASE + "/invoices", app=rt.apps[L.SAFARI_PID][0])
    assert notes == [("AWOS needs you", "AWOS needs you: log in to Stripe")]
    assert r.outcome is not A.Outcome.REFUSED and r.detail["human_checkpoint"] == "login"
    assert r.after.url == BASE + "/invoices"
    assert any(d.startswith("needs_you: login on Stripe (dashboard.stripe.com)") for d in digest)
    assert any(d.startswith("resumed: login on Stripe") for d in digest)
    gaps = [b - a for a, b in zip(reads, reads[1:])]
    assert gaps and all(abs(g - 5.0) < 1.0 for g in gaps[-3:])                # polled every ~5 s
    # the owner's session page never got typed into
    assert not saf.posts


@pytest.mark.parametrize("html,kind", [(OTP, "otp"), (CAPTCHA, "captcha"), (EXPIRED, "session_expired"),
                                       (UNUSUAL, "unusual_activity")])
def test_precheck_waits_for_owner_then_acts_on_fresh_page(html, kind):
    pages = {BASE + "/statements/2026-09.pdf": STATEMENT}
    rt, saf, w, pa, notes, digest = setup(pages)
    snap = show(rt, saf, w, BASE + "/x", html)
    rt.at(rt.t + 20, lambda: saf.show(w, BASE + "/invoices", INVOICES))
    r = pa.press(snap, Target(role="link", name="Download statement"))
    assert len(notes) == 1 and r.detail.get("human_checkpoint") == kind
    assert r.outcome is A.Outcome.VERIFIED and r.after.url.endswith("2026-09.pdf")


def test_timeout_is_needs_you():
    rt, saf, w, pa, notes, digest = setup({}, timeout_s=60)
    snap = show(rt, saf, w, BASE + "/verify", OTP)
    t0 = rt.t
    r = pa.press(snap, Target(role="button", name="Verify"))
    assert r.outcome is A.Outcome.REFUSED and r.reason.startswith(A.NEEDS_USER) and "needs_you" in r.reason
    assert r.detail["needs_you"]["kind"] == "otp"
    assert 60 <= rt.t - t0 < 70
    assert any("not cleared after 60 s" in d for d in digest)
    assert pa.checkpoint.events[-1]["outcome"] == "timeout"


def test_default_timeout_is_ten_minutes_and_poll_five_seconds():
    rt, saf, w, pa, *_ = setup({})
    cp = C.Checkpoint(SRC, clock=rt, notify=lambda *a: None)
    assert cp.timeout_s == 600 and cp.poll_s == 5
    snap = show(rt, saf, w, BASE + "/login", LOGIN)
    t0 = rt.t
    with pytest.raises(C.HumanTimeout) as e:
        cp.wait(lambda: snap_of(rt, w), cp.human_needed(snap, cp.detect(snap)))
    assert e.value.kind == "login" and 600 <= rt.t - t0 < 606


def test_resume_requires_allowed_domain():
    rt, saf, w, pa, notes, digest = setup({}, timeout_s=30)
    snap = show(rt, saf, w, BASE + "/login", LOGIN)
    rt.at(rt.t + 10, lambda: saf.show(w, "https://elsewhere.example/home", STATEMENT))   # clear page, wrong domain
    r = pa.press(snap, Target(role="button", name="Sign in"))
    assert r.outcome is A.Outcome.REFUSED and r.detail["needs_you"]["kind"] == "login"


def test_page_changed_under_path_target_is_not_blind_clicked():
    rt, saf, w, pa, *_ = setup({})
    snap = show(rt, saf, w, BASE + "/x", EXPIRED)
    path = next(p for p in snap.find(lambda n: n.get("title") == "Log in"))
    rt.at(rt.t + 10, lambda: saf.show(w, BASE + "/invoices", INVOICES))
    r = pa.press(snap, path)
    assert r.outcome is A.Outcome.REFUSED and "re-observe" in r.reason
    assert r.after is not None and r.after.url == BASE + "/invoices"


# ------------------------------------------------------------------- pacing --
def test_human_pace_between_actions():
    pages = {BASE + "/statements/2026-09.pdf": STATEMENT, BASE + "/invoices": INVOICES}
    rt, saf, w, pa, *_ = setup(pages)
    starts = []
    real = pa.actor.press
    pa.actor.press = lambda *a, **k: starts.append(rt.t) or real(*a, **k)
    for _ in range(3):
        snap = show(rt, saf, w, BASE + "/invoices", INVOICES)
        pa.press(snap, Target(role="link", name="Download statement"))
    assert len(starts) == 3
    assert len(pa.pacer.waits) == 2 and all(1.5 <= x <= 3.0 for x in pa.pacer.waits)


def test_pacer_bounds_and_validation():
    import random
    rt = Human()
    p = C.Pacer(1.5, 3.0, clock=rt, rng=random.Random(7))
    assert p.wait() == 0.0
    for _ in range(20):
        p.done()
        w = p.wait()
        assert 1.5 <= w <= 3.0
    with pytest.raises(ValueError):
        C.Pacer(3.0, 1.0)


# --------------------------------------------------------------- host guard --
def test_host_mac_refused_and_no_notification():
    rt = Human()
    saf = PortalSafari(rt, {})
    w = saf.new_window()
    notes = []
    pa = C.PackActor(A.Actor(rt, vm_check=lambda: False), SRC, notify=lambda t, m: notes.append(m))
    snap = show(rt, saf, w, BASE + "/login", LOGIN)
    for r in (pa.press(snap, Target(role="button", name="Sign in")), pa.navigate(w, BASE + "/invoices"),
              pa.open_url(BASE + "/invoices"), pa.scroll(snap)):
        assert r.outcome is A.Outcome.REFUSED and "guest VM" in r.reason
    assert not notes and rt.t < 5


def test_mac_notify_never_runs_on_host(monkeypatch):
    calls = []
    monkeypatch.setattr(C.subprocess, "run", lambda *a, **k: calls.append(a))
    assert C.mac_notify("t", "m", vm_check=lambda: False) is False
    assert not calls
