"""AWOS Replay 0.6.1: the monthly portal run needs the human as rarely as possible (docs/specs/portal_run.md section 5).

  - keep-alive: each portal's start page in a background tab, one at a time; logged in vs login page per portal;
    a logout notifies once; "Login needed" in the checklist; nothing clicked or typed; skipped while busy
  - pre-flight: at the start of a collection run, ONE notification naming the logged-out portals; the ready portal
    runs first; "Open the ones that need me" opens just that login; the portal is picked up once its login is seen.
    Before a scheduled run: an alarm preflightHours ahead, one notification
  - remember me: on a login handoff only a "Keep me signed in"-like checkbox is ticked, never an unrelated one; Option off
  - Chrome autofill sign-in: clicks Sign in only when both fields are marked autofilled; the password value never
    appears in the run log, the run state, storage or the notifications. Playwright cannot make Chrome autofill a saved
    password, so the fixture (login_autofill.html) marks the fields with data-awos-test-autofill, which the extension
    honours only when the worker's test hook (chrome.storage.session) is set; the real check is :autofill.
Run:  PYTHONPATH=. ~/.awos/rl-venv/bin/python -m pytest tests/extension -q
"""
import json
import time

import pytest

from test_portal_run import _click_download, _paused, _record, _record_payouts, _record_statements, _wait, page_libs  # noqa: F401

pytestmark = pytest.mark.timeout(180)
SECRET = "SECRET-PW-4417"


def _fast(x, **kw):
    s = dict(keepAliveSpacingMs=100, preflightSpacingMs=0, loginPollMs=300, loginProbeMs=800, collectionWaitMin=2)
    s.update(kw)
    x.set_settings(**s)


def _supplier(x):
    _, _, sup = _record(x, "/portal/supplier.html", "Supplier statement", lambda p: _click_download(p, "#sup"))
    x.call("save_recording", id=sup["id"], expect={"urlPattern": "", "texts": []},
           expectDownload={"nameRegex": r"supplier-statement-{{year}}-{{month}}\.pdf$"})
    return sup


def _notices(x, prefix):
    return [n for n in x.sw.evaluate("NOTICES") if n["id"].startswith(prefix)]


def _tab_count(x):
    return x.sw.evaluate("chrome.tabs.query({}).then(ts => ts.length)")


# ---- 1. session keep-alive ---------------------------------------------------------------------------------------

def test_keep_alive_marks_logged_in_and_logged_out_portals_and_notifies_once(x):
    x.ctx.clear_cookies()
    sup = _supplier(x)
    _, _, pay = _record_payouts(x, "Payout report")
    _fast(x)
    col = x.call("coll_save", name="Month-end", recIds=[sup["id"], pay["id"]])
    alarm = x.sw.evaluate("(id) => chrome.alarms.get('awos-keep:' + id)", col["id"])
    days = (alarm["scheduledTime"] / 1000 - time.time()) / 86400
    assert 2.9 < days < 3.1 and col["keepNext"] == alarm["scheduledTime"], (days, alarm)       # on by default, every 3 days

    x.server.auth["/portal/payouts.html"] = "/portal/login_remember.html"      # the payout portal logged the user out
    x.server.hits.clear()
    tabs = _tab_count(x)
    out = x.sw.evaluate("(id) => keepAliveSweep(id)", col["id"])              # what the alarm runs
    st = {r["recId"]: r["status"] for r in out["results"]}
    assert st == {sup["id"]: "ok", pay["id"]: "login"}, out
    ps = x.state()["portalStatus"]
    assert ps[pay["id"]]["status"] == "login" and ps[pay["id"]]["at"] and ps[sup["id"]]["status"] == "ok", ps
    assert _tab_count(x) == tabs                                               # each check's tab was closed
    assert "/portal/dashboard.html" not in x.server.hits                       # nothing was submitted (no click, no typing)
    assert not [h for h in x.server.hits if h.startswith("/files/")]           # and nothing downloaded
    n = _notices(x, "awos-keep-")
    assert len(n) == 1 and "Payout report logged you out; log in any time before the month-end run" in n[0]["message"], n
    assert n[0]["buttons"] == ["Open the ones that need me"]

    x.sw.evaluate("(id) => keepAliveSweep(id)", col["id"])                     # still logged out: no second notice
    assert len(_notices(x, "awos-keep-")) == 1

    popup = x.popup(0)
    popup.wait_for_selector(f"li[data-coll='{col['id']}'] .portal[data-rec='{pay['id']}']")
    assert popup.inner_text(f"li[data-coll='{col['id']}'] .portal[data-rec='{pay['id']}']").find("Login needed") >= 0
    assert "Login needed" not in popup.inner_text(f"li[data-coll='{col['id']}'] .portal[data-rec='{sup['id']}']")
    assert "Open the ones that need me (1)" in popup.inner_text(f"li[data-coll='{col['id']}'] button.collopen")

    # the user is mid-run (here: recording): the sweep does not run, it tries again later
    x.sw.evaluate("chrome.storage.session.set({recState: {tabId: -1, rec: {steps: []}}})")
    out = x.sw.evaluate("(id) => keepAliveSweep(id)", col["id"])
    assert "recording" in out["skipped"], out
    x.sw.evaluate("chrome.storage.session.remove('recState')")

    # logged in again, then logged out again later: a new logout is reported again
    x.server.auth.clear()
    x.sw.evaluate("(id) => keepAliveSweep(id)", col["id"])
    assert x.state()["portalStatus"][pay["id"]]["status"] == "ok"
    x.server.auth["/portal/payouts.html"] = "/portal/login.html"
    x.sw.evaluate("(id) => keepAliveSweep(id)", col["id"])
    assert len(_notices(x, "awos-keep-")) == 2
    off = x.call("coll_save", id=col["id"], keepAlive=False)
    assert off["keepNext"] is None and not x.sw.evaluate("(id) => chrome.alarms.get('awos-keep:' + id)", col["id"])


# ---- 2. pre-flight -------------------------------------------------------------------------------------------------

def test_preflight_lists_logged_out_portals_once_runs_ready_first_and_picks_up_after_login(x):
    x.ctx.clear_cookies()
    sup = _supplier(x)
    _, _, pay = _record_payouts(x, "Payout report")
    _fast(x, loginProbeMs=60000)          # after its first look, the waiting run watches the login tab, not new probe tabs
    col = x.call("coll_save", name="Month-end", recIds=[pay["id"], sup["id"]])      # payouts first in the list
    x.server.auth["/portal/payouts.html"] = "/portal/login.html"
    x.server.hits.clear()
    x.call("coll_run", id=col["id"], period="2026-10")
    c = _wait(lambda: (lambda c: c if c and c.get("waitingLogin") else None)(x.state()["coll"]), timeout=60,
              what="the collection to wait for the payout login")
    assert c["needLogin"] == [pay["id"]] and c["queue"] == [sup["id"], pay["id"]], c
    res = x.state()["collResults"][col["id"]]["2026-10"]
    assert res[sup["id"]]["status"] == "got", res                               # the ready portal ran first
    assert res[pay["id"]]["status"] == "needs_you" and res[pay["id"]]["loginNeeded"] and res[pay["id"]]["waiting"], res
    pre = _notices(x, "awos-pre-")
    assert len(pre) == 1 and "Log in to Payout report" in pre[0]["message"] and "1 portal is ready" in pre[0]["message"], pre
    assert pre[0]["buttons"] == ["Open the ones that need me"]
    assert not _notices(x, "awos-run-"), "the run itself was not interrupted by a handoff"

    opened = x.call("coll_open_logins", id=col["id"])
    assert opened["recIds"] == [pay["id"]], opened                              # just the one that needs the user
    login = _wait(lambda: next((p for p in x.ctx.pages if p.url.endswith("/portal/login.html")), None), what="the login tab")
    login.wait_for_selector("#pw")
    assert login.input_value("#pw") == ""                                       # AWOS never types a password
    login.fill("#email", "ap@payouts.example")
    login.fill("#pw", "typed by the user")
    login.click("#signin")
    login.wait_for_url("**/portal/dashboard.html")
    c = _wait(lambda: (lambda c: c if c and c["status"] == "done" else None)(x.state()["coll"]), timeout=60, what="the collection to finish")
    res = x.state()["collResults"][col["id"]]["2026-10"]
    assert res[pay["id"]]["status"] == "got" and res[pay["id"]]["file"] == "payout-2026-10.csv", res
    assert c["summary"] == "2 of 2 got" and c["pickedUp"] == [pay["id"]], c
    order = [r["recId"] for r in reversed(x.state()["runs"][:2])]
    assert order == [sup["id"], pay["id"]], order
    assert len(_notices(x, "awos-pre-")) == 1
    assert x.server.hits.count("/files/payout-2026-10.csv") == 1


def test_preflight_before_a_scheduled_run(x):
    x.ctx.clear_cookies()
    sup = _supplier(x)
    _, _, pay = _record_payouts(x, "Payout report")
    _fast(x)
    col = x.call("coll_save", name="Month-end", recIds=[sup["id"], pay["id"]], schedule={"kind": "monthly", "date": 28, "time": "23:30"})
    run_at = x.sw.evaluate("(id) => chrome.alarms.get('awos-coll-sched:' + id)", col["id"])["scheduledTime"]
    pre = x.sw.evaluate("(id) => chrome.alarms.get('awos-pre:' + id)", col["id"])
    if run_at - 24 * 3600e3 > time.time() * 1000 + 60e3:
        assert pre and abs(pre["scheduledTime"] - (run_at - 24 * 3600e3)) < 1000, (pre, run_at)
    x.call("coll_save", id=col["id"], preflightHours=6)
    pre = x.sw.evaluate("(id) => chrome.alarms.get('awos-pre:' + id)", col["id"])
    if run_at - 6 * 3600e3 > time.time() * 1000 + 60e3:
        assert pre and abs(pre["scheduledTime"] - (run_at - 6 * 3600e3)) < 1000, (pre, run_at)
    x.server.auth["/portal/payouts.html"] = "/portal/login.html"
    out = x.sw.evaluate("(id) => firePreflight(id)", col["id"])                  # what the alarm runs
    assert out["need"] == [pay["id"]], out
    n = _notices(x, "awos-pre-")
    assert len(n) == 1 and "Log in to Payout report any time before then" in n[0]["message"], n
    assert x.state()["collections"][col["id"]]["lastPreflight"]["need"] == [pay["id"]]
    x.call("coll_save", id=col["id"], preflightHours=0)                         # 0 = no advance check
    assert not x.sw.evaluate("(id) => chrome.alarms.get('awos-pre:' + id)", col["id"])


# ---- 3. remember me ------------------------------------------------------------------------------------------------

def test_remember_me_is_ticked_on_a_login_handoff_and_nothing_else(x):
    page, tab, rec = _record_statements(x)
    page.goto(x.server.base + "/help.html")
    x.server.redirects["/portal/older.html"] = "/portal/login_remember.html"
    x.call("run", recId=rec["id"], tabId=tab)
    run = _paused(x)
    assert run["status"] == "needs_user" and run["pending"]["handoff"] == "login", run["log"]
    assert page.evaluate("[keep.checked, news.checked, terms.checked]") == [True, False, False]
    assert any('ticked "keep me signed in"' in l["text"] for l in run["log"]), run["log"]
    assert page.input_value("#pw") == ""
    x.call("reject", runId=run["id"], nonce=run["pending"]["nonce"])
    x.wait_run()
    # Option off: nothing is ticked
    x.set_settings(rememberMe=False)
    page.goto(x.server.base + "/help.html")
    x.call("run", recId=rec["id"], tabId=tab)
    run = _paused(x)
    assert run["pending"]["handoff"] == "login"
    assert page.evaluate("[keep.checked, news.checked, terms.checked]") == [False, False, False]
    assert not any("ticked" in l["text"] for l in run["log"])


def test_remember_me_vocabulary(page_libs):
    p = page_libs("/help.html")
    r = p.evaluate("""() => {
      const labels = ['Remember me', 'Keep me signed in', 'Stay signed in?', 'Keep me logged in', 'Remember this device',
        'Trust this device for 30 days', "Don't ask again on this device", 'Don’t ask again', 'Remember me for 14 days',
        'Remember me and send me offers', 'I agree to the terms', 'Subscribe to our newsletter', 'Remember my cookie choices',
        'Keep me signed in and accept marketing', 'Sign me up'];
      document.body.innerHTML = labels.map((l, i) => '<label><input type=checkbox id=c' + i + '> ' + l.replace(/</g, '') + '</label>').join('<br>');
      return labels.map((l, i) => [l, AWOSPortal.REMEMBER_RE.test(AWOSPortal.rememberLabel(document.getElementById('c' + i)))]);
    }""")
    want = {l: ok for l, ok in r}
    assert all(want[l] for l in ["Remember me", "Keep me signed in", "Stay signed in?", "Keep me logged in", "Remember this device",
                                 "Trust this device for 30 days", "Don't ask again on this device", "Don’t ask again",
                                 "Remember me for 14 days"]), r
    assert not any(want[l] for l in ["Remember me and send me offers", "I agree to the terms", "Subscribe to our newsletter",
                                     "Remember my cookie choices", "Keep me signed in and accept marketing", "Sign me up"]), r
    # an already ticked box is left alone (never unticked); an unrelated box is never ticked
    r = p.evaluate("""() => {
      document.body.innerHTML = '<label><input type=checkbox id=a> Subscribe to our newsletter</label>'
        + '<label><input type=checkbox id=b checked> Remember me</label>';
      const out = AWOSPortal.rememberMe();
      return [out.ticked, a.checked, b.checked];
    }""")
    assert r == ["", False, True], r


# ---- 4. Chrome-autofilled logins (experimental) --------------------------------------------------------------------

def _all_state(x):
    return json.dumps([x.sw.evaluate("chrome.storage.local.get(null)"), x.sw.evaluate("chrome.storage.session.get(null)"),
                       x.sw.evaluate("NOTICES"), x.state()])


def test_autofilled_login_signs_in_once_and_the_password_never_leaves_the_page(x):
    x.ctx.clear_cookies()
    page, tab, rec = _record_statements(x)
    page.goto(x.server.base + "/help.html")
    x.sw.evaluate("chrome.storage.session.set({testAutofillHook: true})")
    x.server.hits.clear()
    x.server.auth["/portal/older.html"] = "/portal/login_autofill.html"        # the session expired after two downloads
    x.call("run", recId=rec["id"], tabId=tab)
    run = x.wait_run(statuses=("needs_user", "awaiting_approval", "done", "failed"), timeout=60)
    assert run["status"] == "done" and run["result"]["verdict"] == "VERIFIED", run["log"]
    log = [l["text"] for l in run["log"]]
    assert any("Chrome had autofilled the username and the password; clicked \"Sign in\" once" in t for t in log), log
    assert any('ticked "remember me on this device"' in t for t in log), log
    assert x.server.hits.count("/portal/dashboard.html") == 1                   # signed in exactly once
    for f in ("03", "04", "02"):
        assert x.server.hits.count(f"/files/acct-statement-2026-{f}.pdf") == 1
    assert not _notices(x, "awos-run-"), "nobody was asked"
    assert SECRET not in _all_state(x)                                         # never read, logged or stored


def test_autofill_needs_both_fields_marked_else_it_hands_off(x):
    x.ctx.clear_cookies()
    page, tab, rec = _record_statements(x)
    page.goto(x.server.base + "/help.html")
    x.sw.evaluate("chrome.storage.session.set({testAutofillHook: true})")
    x.server.auth["/portal/older.html"] = "/portal/login_autofill.html?mark=pw"   # only the password looks autofilled
    x.call("run", recId=rec["id"], tabId=tab)
    run = _paused(x)
    assert run["status"] == "needs_user" and run["pending"]["handoff"] == "login", run["log"]
    assert page.evaluate("window.__signin") is None and page.evaluate("window.__forgot") is None
    assert any("not signing in with Chrome's saved password: Chrome has not autofilled the username" in l["text"] for l in run["log"]), run["log"]
    assert SECRET not in _all_state(x)
    x.call("reject", runId=run["id"], nonce=run["pending"]["nonce"])
    x.wait_run()
    # Option off: no click even when both are marked
    x.set_settings(autofillSignin=False)
    x.server.auth["/portal/older.html"] = "/portal/login_autofill.html"
    page.goto(x.server.base + "/help.html")
    x.call("run", recId=rec["id"], tabId=tab)
    run = _paused(x)
    assert run["pending"]["handoff"] == "login" and page.evaluate("window.__signin") is None, run["log"]
    assert SECRET not in _all_state(x)


def test_autofill_detector_reads_the_autofill_state_not_the_value(page_libs):
    p = page_libs("/help.html")
    r = p.evaluate("""() => {
      const form = (marks) => {
        document.body.innerHTML = '<form onsubmit="event.preventDefault(); window.__sub = (window.__sub||0) + 1">'
          + '<input id=u type=email' + (marks.u ? ' data-awos-test-autofill' : '') + '>'
          + '<input id=p type=password' + (marks.p ? ' data-awos-test-autofill' : '') + '>'
          + '<button type=button onclick="window.__forgot=1">Forgot password?</button><button type=submit>Sign in</button></form>';
        u.value = 'someone@example.com'; p.value = 'SECRET-PW-4417'; window.__sub = 0;
      };
      const out = {};
      form({u: 1, p: 1}); out.noHook = [AWOSPortal.autofillSignin(false).clicked, window.__sub];      // values alone: no
      form({u: 1, p: 1}); out.both = [AWOSPortal.autofillSignin(true).clicked, window.__sub, window.__forgot || 0];
      form({p: 1}); out.pwOnly = [AWOSPortal.autofillSignin(true).clicked, window.__sub];
      form({u: 1}); out.userOnly = [AWOSPortal.autofillSignin(true).clicked, window.__sub];
      out.text = JSON.stringify(AWOSPortal.autofillSignin(true));
      return out;
    }""")
    assert r["noHook"] == ["", 0], r
    assert r["both"] == ["Sign in", 1, 0], r
    assert r["pwOnly"] == ["", 0] and r["userOnly"] == ["", 0], r
    assert "SECRET" not in r["text"] and "someone" not in r["text"], r
