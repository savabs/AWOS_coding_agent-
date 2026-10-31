"""AWOS Replay 0.6 portal runs, end to end on local fixture portals (tests/extension/pages/portal):

  - handoff: a supplier portal whose session expires mid-run (302 to its login page) pauses, the user logs in (lands
    on the dashboard), Continue goes back to the step's page and finishes; the 2 statements already downloaded are not
    downloaded again; a one-time-code page and a bot-check page hand off the same way (the check is never touched)
  - persistence: the paused run survives a worker restart; a download step whose file already arrived is skipped
  - pop-ups: a "what's new" modal is dismissed with "Not now", a cookie banner only with "Reject all", a modal that
    only offers "Accept and continue" is never clicked (the run pauses for the user)
  - new-tab files: a report link that opens a PDF in a new tab is downloaded, the tab closed, the file counted
  - collections: 3 portals -> checklist Got / Missing / Needs you per period, summary notice, a re-run skips Got
Run:  PYTHONPATH=. ~/.awos/rl-venv/bin/python -m pytest tests/extension -q
"""
import time

import pytest

pytestmark = pytest.mark.timeout(180)


def _wait(fn, timeout=30, what="condition"):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.2)
    raise AssertionError("timed out waiting for " + what)


def _downloads(x, needle):
    """completed downloads whose URL contains `needle` (the browser's list, as the extension sees it)."""
    return x.sw.evaluate("(n) => chrome.downloads.search({}).then(ds => ds.filter(d => d.url.indexOf(n) >= 0 && d.state === 'complete').length)", needle)


def _hits(x, path):
    return x.server.hits.count(path)


def _record(x, path, name, actions):
    page = x.ctx.new_page()
    page.goto(x.server.base + path)
    tab = x.tab_id(page)
    x.call("start_recording", tabId=tab, name=name)
    actions(page)
    time.sleep(0.7)
    rec = x.call("stop_recording")
    return page, tab, rec


def _click_download(page, sel):
    with page.expect_download():
        page.click(sel)
    time.sleep(0.4)


def _record_statements(x):
    def acts(page):
        _click_download(page, "#mar")
        _click_download(page, "#apr")
        page.click("#older")
        page.wait_for_url("**/portal/older.html")
        time.sleep(0.4)
        _click_download(page, "#feb")
    page, tab, rec = _record(x, "/portal/statements.html", "Supplier statements", acts)
    rec = x.call("save_recording", id=rec["id"], goal="download the account statements",
                 expect={"urlPattern": "", "texts": []}, expectDownload={"nameRegex": r"acct-statement-2026-02\.pdf$"})
    return page, tab, rec


def _paused(x, timeout=45):
    return x.wait_run(statuses=("needs_user", "awaiting_approval", "done", "failed"), timeout=timeout)


def _restart_worker(x):
    """stop the extension's service worker for real (CDP Target.closeTarget), then wake a fresh one: in-memory state
    is gone, chrome.storage.session stays, as when Chrome stops an idle worker during a long pause."""
    old = x.sw
    old.evaluate("self.__before_restart = 1")
    cdp = x.ctx.new_cdp_session(x.ctx.pages[0])
    sw = [t for t in cdp.send("Target.getTargets")["targetInfos"] if t["type"] == "service_worker" and x.id in t["url"]]
    cdp.send("Target.closeTarget", {"targetId": sw[0]["targetId"]})
    cdp.detach()
    time.sleep(1.0)
    p = x.ctx.new_page()
    p.goto(f"chrome-extension://{x.id}/options.html")          # any extension page wakes the worker
    def fresh():
        for w in reversed(x.ctx.service_workers):
            try:
                if w.evaluate("self.__before_restart ? 0 : 1"):
                    return w
            except Exception:
                pass
        return None
    x.sw = _wait(fresh, timeout=15, what="a fresh service worker")
    p.close()


def _continue(x, run):
    x.call("continue", runId=run["id"], nonce=run["pending"]["nonce"])


# ---- 1. handoff: session expired mid-run ----------------------------------------------------------------------

def test_login_expiry_mid_run_hands_off_and_resumes_without_redoing_downloads(x):
    page, tab, rec = _record_statements(x)
    acts = [(s["action"], (s.get("download") or {}).get("name")) for s in rec["steps"]]
    assert acts == [("goto", None), ("click", "acct-statement-2026-03.pdf"), ("click", "acct-statement-2026-04.pdf"),
                    ("click", None), ("click", "acct-statement-2026-02.pdf")], acts
    page.goto(x.server.base + "/help.html")
    x.server.hits.clear()
    x.server.redirects["/portal/older.html"] = "/portal/login.html"        # the session expired after two downloads
    x.call("run", recId=rec["id"], tabId=tab)
    run = _paused(x)
    assert run["status"] == "needs_user" and run["pending"]["handoff"] == "login", run["log"]
    assert run["i"] == 4 and sorted(run["done"]) == ["0", "1", "2", "3"], run
    assert "Log in in this tab, then click Continue" in run["pending"]["message"]
    assert any("needs you" in n["title"] for n in x.sw.evaluate("NOTICES")), "no notification for the handoff"
    assert page.input_value("#pw") == ""                                    # AWOS never types a password
    # the user logs in; the portal lands on its dashboard, not where the run was
    x.server.redirects.clear()
    page.fill("#email", "ap@supplier.example")
    page.fill("#pw", "typed by the user")
    page.click("#signin")
    page.wait_for_url("**/portal/dashboard.html")
    _continue(x, run)
    run = x.wait_run()
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    assert any("going back to" in l["text"] and "/portal/older.html" in l["text"] for l in run["log"]), run["log"]
    for f in ("03", "04", "02"):
        assert _hits(x, f"/files/acct-statement-2026-{f}.pdf") == 1, (f, x.server.hits)


def test_one_time_code_page_hands_off_and_resumes(x):
    page, tab, rec = _record_statements(x)
    page.goto(x.server.base + "/help.html")
    x.server.redirects["/portal/older.html"] = "/portal/otp.html"
    x.call("run", recId=rec["id"], tabId=tab)
    run = _paused(x)
    assert run["status"] == "needs_user" and run["pending"]["handoff"] == "otp", run["log"]
    assert "Enter the code in this tab" in run["pending"]["message"]
    x.server.redirects.clear()
    page.fill("#code", "123456")                       # the user types the code; the portal goes on to the page
    page.click("text=Verify")
    page.wait_for_url("**/portal/older.html")
    _continue(x, run)
    run = x.wait_run()
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    assert any("on step 5's page; continuing" in l["text"] for l in run["log"]), run["log"]


def test_bot_check_is_handed_off_and_never_touched(x):
    def acts(page):
        _click_download(page, "#sup")
    page, tab, rec = _record(x, "/portal/supplier.html", "Supplier documents", acts)
    x.call("save_recording", id=rec["id"], expect={"urlPattern": "", "texts": []},
           expectDownload={"nameRegex": r"supplier-statement-{{year}}-{{month}}\.pdf$"})
    page.goto(x.server.base + "/help.html")
    x.server.redirects["/portal/supplier.html"] = "/portal/check.html"
    x.call("run", recId=rec["id"], tabId=tab)
    run = _paused(x)
    assert run["status"] == "needs_user" and run["pending"]["handoff"] == "captcha", run["log"]
    assert "never solves" in run["pending"]["message"] and "Solve the check in this tab" in run["pending"]["message"]
    assert page.evaluate("window.__touched") == 0
    _continue(x, run)                                  # Continue while the check is still there: it waits again
    run = _paused(x)
    assert run["status"] == "needs_user" and run["pending"]["message"].startswith("Still waiting"), run["log"]
    assert page.evaluate("window.__touched") == 0
    x.server.redirects.clear()                         # the user solved it; the site sends them on
    page.goto(x.server.base + "/portal/supplier.html")
    _continue(x, run)
    run = x.wait_run()
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    assert run["result"]["checks"][-1]["actual"] == "supplier-statement-2026-10.pdf" or "{{" not in run["result"]["checks"][-1]["expected"]


# ---- 2. persistence across a worker restart --------------------------------------------------------------------

def test_paused_run_survives_worker_restart_and_skips_finished_download(x):
    page, tab, rec = _record_statements(x)
    page.goto(x.server.base + "/help.html")
    x.server.hits.clear()
    x.server.redirects["/portal/older.html"] = "/portal/login.html"
    x.call("run", recId=rec["id"], tabId=tab)
    run = _paused(x)
    assert run["pending"]["handoff"] == "login"
    # the worker really restarts: in-memory state is gone, storage.session keeps the run, which stays paused
    _restart_worker(x)
    out = _wait(lambda: x.state()["run"], what="the run after the restart")
    assert out["status"] == "needs_user" and out["pending"]["nonce"] == run["pending"]["nonce"], out
    assert sorted(out["done"]) == ["0", "1", "2", "3"] and out["pending"]["handoff"] == "login"
    # as if the April click ran but its completion was never saved: the step is not done, its file is there
    x.sw.evaluate("""chrome.storage.session.get('run').then(o => { var r = o.run; r.i = 2; delete r.done['2']; delete r.done['3'];
                     return chrome.storage.session.set({run: r}); })""")
    x.server.redirects.clear()
    page.fill("#pw", "typed by the user")
    page.click("#signin")
    page.wait_for_url("**/portal/dashboard.html")
    _continue(x, run)
    run = x.wait_run()
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    assert any("acct-statement-2026-04.pdf was already downloaded in this run; skipped" in l["text"] for l in run["log"]), run["log"]
    assert _hits(x, "/files/acct-statement-2026-04.pdf") == 1, x.server.hits
    assert _hits(x, "/files/acct-statement-2026-02.pdf") == 1


# ---- 3. pop-ups after login --------------------------------------------------------------------------------------

def _record_payouts(x, name="Payouts"):
    page, tab, rec = _record(x, "/portal/payouts.html", name, lambda p: _click_download(p, "#oct"))
    x.call("save_recording", id=rec["id"], expect={"urlPattern": "", "texts": []},
           expectDownload={"nameRegex": r"payout-{{year}}-{{month}}\.csv$"})
    return page, tab, rec


def _run_with(x, page, tab, rec, variant):
    page.goto(x.server.base + "/help.html")
    x.server.overrides["/portal/payouts.html"] = "portal/" + variant
    x.call("run", recId=rec["id"], tabId=tab, params={})
    return _paused(x)


def test_modal_after_login_is_dismissed_then_step_succeeds(x):
    page, tab, rec = _record_payouts(x)
    run = _run_with(x, page, tab, rec, "payouts_modal.html")
    assert run["status"] == "done" and run["result"]["verdict"] == "VERIFIED", run["log"]
    assert page.evaluate("[window.__dismissed, window.__tour]") == [1, None]
    assert any('dismissed a pop-up' in l["text"] and 'with "Not now"' in l["text"] for l in run["log"]), run["log"]
    assert run["dismissed"] == [{"step": 1, "kind": "popup", "control": "Not now"}]


def test_consent_banner_is_only_rejected(x):
    page, tab, rec = _record_payouts(x)
    run = _run_with(x, page, tab, rec, "payouts_consent.html")
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    assert page.evaluate("[window.__rejected, window.__accepted]") == [1, None]
    assert any('cookie / consent banner' in l["text"] and 'with "Reject all"' in l["text"] for l in run["log"]), run["log"]


def test_accept_only_modal_is_not_clicked_and_pauses(x):
    page, tab, rec = _record_payouts(x)
    run = _run_with(x, page, tab, rec, "payouts_terms.html")
    assert run["status"] == "needs_user" and run["pending"].get("popup") == "popup", run["log"]
    assert "Accept and continue" in run["pending"]["message"] and "yourself" in run["pending"]["message"]
    assert page.evaluate("window.__agreed") is None
    page.evaluate("document.getElementById('ov').remove()")      # the user reads the terms and closes it themselves
    _continue(x, run)
    run = x.wait_run()
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    assert page.evaluate("window.__agreed") is None


def test_dismiss_vocabulary_never_accepts(page_libs):
    p = page_libs("/help.html")
    r = p.evaluate("""() => {
      document.body.innerHTML = '<div id=d role=dialog aria-label="Cookie settings">We use cookies'
        + '<button>Accept all</button><button>Allow all cookies</button><button>I agree</button><button>Reject all</button></div>'
        + '<div id=e role=dialog aria-label="Delete your account?"><button>Not now</button></div>'
        + '<div id=f role=dialog><button>Pay now</button><button>Confirm</button><button>Maybe later</button></div>';
      const P = AWOSPortal, q = s => Array.from(document.querySelectorAll(s));
      return {consent: q('#d button').map(b => [b.textContent, P.allowed(b, 'consent')]),
              del: P.allowed(document.querySelector('#e button'), 'popup'),
              other: q('#f button').map(b => [b.textContent, P.allowed(b, 'popup')])};
    }""")
    assert r["consent"] == [["Accept all", False], ["Allow all cookies", False], ["I agree", False], ["Reject all", True]], r
    assert r["del"] is False                                  # "Not now" inside a delete dialog: the context is a danger word
    assert r["other"] == [["Pay now", False], ["Confirm", False], ["Maybe later", True]], r


# ---- 4. a file that opens in a new tab -----------------------------------------------------------------------------

def test_new_tab_pdf_is_downloaded_closed_and_counted(x):
    def acts(page):
        with x.ctx.expect_page() as viewer:
            page.click("#rep")
        viewer.value.wait_for_load_state()
        time.sleep(0.8)
        viewer.value.close()                                   # the user looked at the PDF and closed it
    page, tab, rec = _record(x, "/portal/reports.html", "Partner report", acts)
    click = [s for s in rec["steps"] if s["action"] == "click"][0]
    assert click.get("opensTab") is True and click["download"]["name"] == "partner-report-2026-10.pdf", rec["steps"]
    x.call("save_recording", id=rec["id"], expect={"urlPattern": "", "texts": []},
           expectDownload={"nameRegex": r"partner-report-{{year}}-{{month}}\.pdf$"})
    before = _downloads(x, "partner-report-2026-10.pdf")
    page.goto(x.server.base + "/help.html")
    x.call("run", recId=rec["id"], tabId=tab)
    run = x.wait_run()
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    assert any("in a new tab" in l["text"] for l in run["log"]), run["log"]
    assert run["fileTabs"][0]["name"].startswith("partner-report-2026-10"), run["fileTabs"]
    assert _downloads(x, "partner-report-2026-10.pdf") - before == 1
    assert not [p for p in x.ctx.pages if "partner-report" in p.url]          # the viewer tab was closed
    recipe = x.call("export_recipe", id=rec["id"])["recipe"]
    rc = [s for s in recipe["steps"] if s["action"] == "click"][0]
    assert rc.get("opens_tab") is True and rc.get("downloads") is True and "partner-report" not in str(rc.get("download", ""))


def test_new_tab_file_from_another_site_asks_first(x):
    """only same-origin / same-site HTTPS files are fetched on their own; another origin waits for Approve."""
    out = x.sw.evaluate("""[autoDownloadOk('http://127.0.0.1:1/a.pdf', 'http://127.0.0.1:1/p'),
                            autoDownloadOk('https://files.portal.example.com/a.pdf', 'https://portal.example.com/p'),
                            autoDownloadOk('http://files.portal.example.com/a.pdf', 'https://portal.example.com/p'),
                            autoDownloadOk('https://cdn.other-host.example/a.pdf', 'https://portal.example.com/p')]""")
    assert out == [True, True, False, False]


# ---- 5. collections: the monthly checklist ---------------------------------------------------------------------------

def _coll_done(x):
    c = x.state()["coll"]
    return c if c and c["status"] == "done" else None


def test_collection_checklist_got_missing_needs_you_and_rerun_skips_got(x):
    _, _, sup = _record(x, "/portal/supplier.html", "Supplier statement", lambda p: _click_download(p, "#sup"))
    x.call("save_recording", id=sup["id"], expect={"urlPattern": "", "texts": []},
           expectDownload={"nameRegex": r"supplier-statement-{{year}}-{{month}}\.pdf$"})
    _, _, pay = _record_payouts(x, "Payout report")
    _, _, par = _record(x, "/portal/partner.html", "Partner statement", lambda p: _click_download(p, "#par"))
    x.call("save_recording", id=par["id"], expect={"urlPattern": "", "texts": []},
           expectDownload={"nameRegex": r"partner-report-{{year}}-{{month}}\.pdf$"})
    col = x.call("coll_save", name="Month-end supplier docs", recIds=[sup["id"], pay["id"], par["id"]])
    assert col["recIds"] == [sup["id"], pay["id"], par["id"]]

    x.server.overrides["/portal/payouts.html"] = "portal/payouts_sept.html"   # only September's payouts are up
    x.server.redirects["/portal/partner.html"] = "/portal/check.html"          # the partner portal shows a bot check
    x.server.hits.clear()
    x.call("coll_run", id=col["id"], period="2026-10")
    run = _wait(lambda: (lambda r: r if r and r["recId"] == par["id"] and r["status"] == "needs_user" else None)(x.state()["run"]),
                timeout=90, what="the partner portal to need the user")
    res = x.state()["collResults"][col["id"]]["2026-10"]
    assert res[par["id"]]["status"] == "needs_you" and res[par["id"]].get("waiting"), res
    popup = x.popup(run["tabId"])
    popup.wait_for_selector(f"li[data-coll='{col['id']}'] button.collskip")
    popup.click(f"li[data-coll='{col['id']}'] button.collskip")              # "Skip this portal": leave it for later
    c = _wait(lambda: _coll_done(x), timeout=30, what="the collection to finish")
    res = x.state()["collResults"][col["id"]]["2026-10"]
    assert res[sup["id"]]["status"] == "got" and res[sup["id"]]["file"] == "supplier-statement-2026-10.pdf", res
    assert res[pay["id"]]["status"] == "missing" and "payout-2026-10" in res[pay["id"]]["reason"], res
    assert res[par["id"]]["status"] == "needs_you", res
    assert c["summary"] == "1 of 3 got, 1 missing, 1 needs you", c
    assert any(n["message"].startswith("1 of 3 got, 1 missing, 1 needs you") for n in x.sw.evaluate("NOTICES"))
    popup.reload()
    row = popup.inner_text(f"li[data-coll='{col['id']}']")
    assert "Got (supplier-statement-2026-10.pdf)" in row and "Missing" in row and "Needs you" in row, row
    assert _hits(x, "/files/supplier-statement-2026-10.pdf") == 1

    # same period again: the supplier portal is already Got and is not run; the payouts are up now
    x.server.overrides["/portal/payouts.html"] = "portal/payouts.html"
    x.call("coll_clear")
    x.call("coll_run", id=col["id"], period="2026-10")
    run = _wait(lambda: (lambda r: r if r and r["recId"] == par["id"] and r["status"] == "needs_user" else None)(x.state()["run"]),
                timeout=90, what="the partner portal again")
    x.call("coll_skip", runId=run["id"])
    c = _wait(lambda: _coll_done(x), timeout=30, what="the second collection run")
    res = x.state()["collResults"][col["id"]]["2026-10"]
    assert res[pay["id"]]["status"] == "got" and res[pay["id"]]["file"] == "payout-2026-10.csv", res
    assert c["summary"] == "2 of 3 got, 1 needs you" and c["alreadyGot"] == 1, c
    assert _hits(x, "/files/supplier-statement-2026-10.pdf") == 1             # the Got portal was not run again
    assert sup["id"] not in [r["recId"] for r in x.state()["runs"][:2]]


def test_collection_monthly_schedule_arms_an_alarm(x):
    _, _, sup = _record(x, "/portal/supplier.html", "Supplier statement", lambda p: _click_download(p, "#sup"))
    col = x.call("coll_save", name="Month-end", recIds=[sup["id"]], schedule={"kind": "monthly", "date": 2, "time": "08:30"})
    alarm = x.sw.evaluate("(id) => chrome.alarms.get('awos-coll-sched:' + id)", col["id"])
    assert alarm and col["next"] == alarm["scheduledTime"]
    when = x.sw.evaluate("(t) => new Date(t).getDate() + ' ' + new Date(t).getHours() + ':' + new Date(t).getMinutes()", alarm["scheduledTime"])
    assert when == "2 8:30"
    assert "day of the month" in x.sw.evaluate(
        "(id) => HANDLERS.coll_save({id: id, schedule: {kind: 'monthly', date: 31, time: '08:30'}}, {}).then(() => '', e => String(e.message))", col["id"])


@pytest.fixture()
def page_libs(server, pw):
    import pathlib
    ext = pathlib.Path(__file__).resolve().parents[2] / "extension"
    try:
        b = pw.chromium.launch(headless=True)
    except Exception as e:
        pytest.skip(f"no Chromium: {e}")
    p = b.new_page()

    def load(path):
        p.goto(server.base + path)
        for lib in ("lib/safety.js", "lib/locator.js", "lib/portal.js"):
            p.add_script_tag(path=str(ext / lib))
        return p
    yield load
    b.close()
