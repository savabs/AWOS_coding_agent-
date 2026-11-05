"""AWOS Replay 0.9.1: the first real-portal failures (an AI-chat app's billing invoices), on a local fixture app
(tests/extension/pages/app/home.html: a profile menu opens a Settings modal dialog with tabs; Billing lists invoices,
each row with an identical "Download PDF"; options come from server.state). No real site, no real model.

  1. the app's own Settings window is never dismissed: not when the run opened it (full route), not when the step was
     recorded in it; a real "What's new" pop-up still is; a Settings window the app reopened OVER the control a step
     needs is closed like a pop-up
  2. identical "Download PDF" rows: the row is picked by its date (full dates with a day), for the run's month; never by
     position alone; "always the newest row" picks the first
  3. a recording that starts inside the open window: warned at start; replay with the window closed walks back in with a
     short goal-guided loop (profile menu -> Settings -> Billing), then performs the recorded step; the prompt names the
     window and tab
  4. downloads: a hosted invoice PAGE on another origin opened in a new tab is followed (recording and replay); a PDF on
     another origin in a new tab waits for Approve and then counts; a PDF built in the page (Blob + <a download>) is
     recorded and counted ("content not checked (generated in the page)"); a recording with no file warns and its replay
     is UNVERIFIED, not green
  5. the invoice list scrolls INSIDE the dialog and loads lazily: the scroll search scrolls the dialog's box
Run:  PYTHONPATH=. ~/.awos/rl-venv/bin/python -m pytest tests/extension -q -k realportal
"""
import time

import pytest

from test_tell_it import Brain

pytestmark = pytest.mark.timeout(240)
HOME = "/app/home.html"


def _wait(fn, timeout=30, what="condition"):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.2)
    raise AssertionError("timed out waiting for " + what)


def _open_billing(page):
    page.click("#prof")
    page.click("#m-settings")
    page.click("[role=tab][data-t=billing]")


def _record(x, actions, state=None, before=None, name="Chat app invoices"):
    x.server.state.clear()
    x.server.state.update(state or {})
    page = x.ctx.new_page()
    page.goto(x.server.base + HOME)
    if before:
        before(page)
    tab = x.tab_id(page)
    started = x.call("start_recording", tabId=tab, name=name)
    actions(page)
    time.sleep(1.0)
    rec = x.call("stop_recording")
    return page, tab, rec, started


def _dl_first(page):
    with page.expect_download():
        page.click("ul.inv li:first-child >> text=Download PDF")
    time.sleep(0.5)


def _run(x, page, tab, rec, state=None, **kw):
    x.server.state.clear()
    x.server.state.update(state or {})
    page.goto(x.server.base + "/help.html")
    x.server.hits.clear()
    x.call("run", recId=rec["id"], tabId=tab, **kw)
    return x.wait_run(statuses=("done", "failed", "needs_user", "awaiting_approval"), timeout=90)


def _logs(run):
    return [l["text"] for l in run["log"]]


def _dl(run):
    return [c for c in run["result"]["checks"] if c["kind"] == "download"][0]


def _full_route(page):
    _open_billing(page)
    _dl_first(page)


# ---- 1 + 2. full route: the run's own Settings window is kept; the row is picked by its date -------------------------

def test_full_route_keeps_own_settings_window_and_picks_row_by_date(x):
    page, tab, rec, _ = _record(x, _full_route)
    clicks = [s for s in rec["steps"] if s["action"] == "click"]
    dl = clicks[-1]
    assert dl.get("variable") and dl["dialog"] == {"name": "Settings", "tab": "Billing"}, dl
    assert dl["loc"]["row"].startswith("Oct 1, 2026") and dl["download"]["name"] == "invoice-2026-10.pdf", dl
    assert rec["expectDownloadProposed"] == r"^invoice-\d+-\d+(?: \(\d+\))?\.pdf$", rec.get("expectDownloadProposed")
    assert not rec.get("startWarning")

    run = _run(x, page, tab, rec)                                   # October (the clock's previous month)
    assert run["result"]["verdict"] == "VERIFIED", _logs(run)
    assert not [t for t in _logs(run) if "dismissed" in t], _logs(run)       # the Settings window was never closed
    assert any("picked the row for October 2026" in t for t in _logs(run)), _logs(run)
    assert "/files/doc/invoice-2026-10.pdf" in x.server.hits and not run.get("modelCalls"), (x.server.hits, run.get("modelCalls"))
    assert "Settings" in run.get("ownDialogs", []), run.get("ownDialogs")

    run = _run(x, page, tab, rec, state={"latest": "2026-11"}, period="2026-11")     # November is the top row now
    assert run["result"]["verdict"] == "VERIFIED", _logs(run)
    assert x.server.hits.count("/files/doc/invoice-2026-11.pdf") == 1 and "/files/doc/invoice-2026-10.pdf" not in x.server.hits, x.server.hits

    run = _run(x, page, tab, rec, state={"latest": "2026-12"}, period="2026-10")     # October is the 3rd row: not by position
    assert run["result"]["verdict"] == "VERIFIED", _logs(run)
    assert "/files/doc/invoice-2026-10.pdf" in x.server.hits and "/files/doc/invoice-2026-12.pdf" not in x.server.hits, x.server.hits

    x.call("save_recording", id=rec["id"], newestRow=True)                            # "always the newest row"
    run = _run(x, page, tab, rec, state={"latest": "2026-12"}, period="2026-10")
    assert any("the newest (first) of 4 rows" in t for t in _logs(run)), _logs(run)
    assert "/files/doc/invoice-2026-12.pdf" in x.server.hits, (x.server.hits, "\n".join(_logs(run)))


def test_promo_popup_dismissed_and_reopened_settings_over_the_page_closed(x):
    page, tab, rec, _ = _record(x, _full_route)
    run = _run(x, page, tab, rec, state={"promo": True})
    assert run["status"] == "done" and run["result"]["verdict"] == "VERIFIED", "\n".join(_logs(run))
    assert any('dismissed a pop-up' in t and '"Got it"' in t for t in _logs(run)), _logs(run)
    # the app reopened Settings (Account tab) over the page: the profile menu the step needs is under it -> closed
    run = _run(x, page, tab, rec, state={"open": "account"})
    assert run["result"]["verdict"] == "VERIFIED", _logs(run)
    assert any('dismissed a pop-up' in t and 'Settings' in t and '"Close"' in t for t in _logs(run)), _logs(run)


# ---- 3. recording that starts inside the open window ---------------------------------------------------------------

def _record_in_window(x):
    return _record(x, _dl_first, before=_open_billing)


def test_start_inside_window_warns_records_context_and_kept_when_open(x):
    page, tab, rec, started = _record_in_window(x)
    assert started["startWarning"].startswith("A window is already open. For a reliable recipe, close it"), started
    assert rec["steps"][0]["dialog"]["name"] == "Settings" and rec["steps"][0]["dialog"]["tab"] == "Billing", rec["steps"][0]
    assert "already open" in rec["warning"], rec.get("warning")
    # replay: the app shows Settings on its Account tab (as in the owner's run): kept, the model moves to Billing
    brain = Brain(['click "Billing"'])
    x.server.model_answer = brain
    run = _run(x, page, tab, rec, state={"open": "account"})
    assert run["result"]["verdict"] == "VERIFIED", _logs(run)
    assert any('kept the "Settings" window open' in t for t in _logs(run)), _logs(run)
    assert not [t for t in _logs(run) if "dismissed" in t], _logs(run)
    assert len(brain.requests) == 1 and "/files/doc/invoice-2026-10.pdf" in x.server.hits, (len(brain.requests), x.server.hits)
    assert any("a move toward the step" in t for t in _logs(run)), _logs(run)


def test_start_inside_window_replayed_closed_walks_back_in(x):
    page, tab, rec, _ = _record_in_window(x)
    brain = Brain(['click "S Saurabh · Max"', 'click "Settings"', 'click "Billing"'])
    x.server.model_answer = brain
    run = _run(x, page, tab, rec)
    assert run["result"]["verdict"] == "VERIFIED", _logs(run)
    assert len(brain.requests) == 3, len(brain.requests)
    p0 = brain.requests[0]["messages"][1]["content"]
    assert 'Last time at this step you clicked the link "Download PDF"' in p0 and '(you were in the "Settings" window, on its "Billing" tab)' in p0, p0
    assert 'The recording started with the "Settings" window already open on its "Billing" tab.' in p0, p0
    assert "give ONE action that leads toward it" in p0
    assert "(2 of 4 such moves used for this step)" in brain.requests[2]["messages"][1]["content"]
    assert "/files/doc/invoice-2026-10.pdf" in x.server.hits
    nav = [g for g in run["guided"] if g.get("nav")]
    assert [g["control"]["name"] for g in nav] == ["S Saurabh · Max", "Settings", "Billing"], run["guided"]
    assert not run.get("updateOffer"), run.get("updateOffer")       # moves are not offered as the step's new target


def test_guided_moves_capped_per_step(x):
    page, tab, rec, _ = _record_in_window(x)
    brain = Brain(['click "Customize"', 'click "Chats and tasks"', 'click "Customize"', 'click "Chats and tasks"', 'click "Customize"'])
    x.server.model_answer = brain
    run = _run(x, page, tab, rec)
    assert run["result"]["verdict"] == "FAILED" and "moves toward" in run["result"]["reason"], run["result"]
    assert len(brain.requests) == 4


# ---- 4. downloads: hosted page followed, other-origin PDF approved, PDF built in the page, no file ------------------

def test_hosted_invoice_page_in_new_tab_is_followed(x):
    def acts(page):
        _open_billing(page)
        with x.ctx.expect_page() as np:
            page.click("ul.inv li:first-child >> text=Download PDF")
        inv = np.value
        inv.wait_for_load_state()
        _wait(lambda: (x.state()["recState"] or {}).get("followed"), what="the recording to follow the new tab")
        with inv.expect_download():
            inv.click("text=Download invoice")
        time.sleep(0.5)
        inv.close()
    page, tab, rec, _ = _record(x, acts, state={"dl": "hosted"})
    clicks = [s for s in rec["steps"] if s["action"] == "click"]
    assert clicks[-2]["followTab"] and clicks[-2]["opensTab"], clicks[-2]
    assert clicks[-1]["loc"]["name"] == "Download invoice" and clicks[-1]["download"]["name"] == "invoice-2026-10.pdf", clicks[-1]
    assert "the recording continued there" in rec["followNote"]
    run = _run(x, page, tab, rec, state={"dl": "hosted"})
    assert run["result"]["verdict"] == "VERIFIED", _logs(run)
    assert any("the recipe continues in that tab" in t for t in _logs(run)), _logs(run)
    assert _dl(run)["ok"] and _dl(run)["actual"].startswith("invoice-2026-10"), _dl(run)


def test_other_origin_pdf_in_new_tab_counts_after_approve(x):
    def acts(page):
        _open_billing(page)
        with x.ctx.expect_page() as viewer:
            page.click("ul.inv li:first-child >> text=Download PDF")
        viewer.value.wait_for_load_state()
        time.sleep(1.0)
        viewer.value.close()
    page, tab, rec, _ = _record(x, acts, state={"dl": "hostedpdf"})
    click = [s for s in rec["steps"] if s["action"] == "click"][-1]
    assert click["opensTab"] and click["download"]["name"] == "invoice-2026-10.pdf" and not click.get("followTab"), click
    run = _run(x, page, tab, rec, state={"dl": "hostedpdf"})
    assert run["status"] == "awaiting_approval" and run["pending"]["danger"] == "file from another site", run.get("pending")
    x.call("approve", runId=run["id"], nonce=run["pending"]["nonce"])
    run = x.wait_run(timeout=60)
    assert run["result"]["verdict"] == "VERIFIED", _logs(run)
    assert _dl(run)["ok"] and _dl(run)["actual"].startswith("invoice-2026-10"), _dl(run)


def test_pdf_built_in_page_blob_is_recorded_and_counted(x):
    page, tab, rec, _ = _record(x, _full_route, state={"dl": "blob"})
    click = [s for s in rec["steps"] if s["action"] == "click"][-1]
    assert click["download"]["name"] == "invoice-2026-10.pdf" and click["download"].get("generated"), click
    assert click["loc"]["row"].startswith("Oct 1, 2026"), click["loc"]
    run = _run(x, page, tab, rec, state={"dl": "blob", "latest": "2026-11"})        # October is the 2nd row now
    assert run["result"]["verdict"] == "VERIFIED", _logs(run)
    d = _dl(run)
    assert d["ok"] and d["actual"].startswith("invoice-2026-10") and d["content"]["state"] == "unchecked", d
    assert "generated in the page" in d["content"]["why"], d


def test_self_closing_download_tab_without_a_pattern_counts_the_file(x):
    # the owner's claude.ai run: "Download PDF" opened a tab that downloaded the invoice and closed itself; no name
    # pattern was saved, so the run ended CHECK. The file this run downloaded now counts as its download check.
    def acts(page):
        _open_billing(page)
        page.click("ul.inv li:first-child >> text=Download PDF")
        time.sleep(1.5)
    page, tab, rec, _ = _record(x, acts, state={"dl": "tabattach"})
    assert not rec.get("expectDownload"), rec.get("expectDownload")        # nothing saved in Goal + checks
    run = _run(x, page, tab, rec, state={"dl": "tabattach"})
    assert run["result"]["verdict"] == "VERIFIED", _logs(run)
    d = _dl(run)
    assert d["ok"] and d.get("implicit"), d                   # (headless Chromium saves downloads under random names)
    assert any("counted the download" in t for t in _logs(run)), _logs(run)


def test_download_click_that_downloads_nothing_warns_and_is_not_green(x):
    def acts(page):
        _open_billing(page)
        page.click("ul.inv li:first-child >> text=Download PDF")
    page, tab, rec, _ = _record(x, acts, state={"dl": "none"})
    assert "No file was downloaded while recording" in rec["warning"], rec.get("warning")
    run = _run(x, page, tab, rec, state={"dl": "none"})
    assert run["result"]["verdict"] == "UNVERIFIED", _logs(run)
    assert "no file was downloaded in this run" in run["result"]["reason"], run["result"]


# ---- 5. the invoice list scrolls inside the dialog -----------------------------------------------------------------

def test_lazy_list_inside_dialog_scroll_box(x):
    def acts(page):
        _open_billing(page)
        page.evaluate("() => { const b = document.getElementById('box'); b.scrollTop = b.scrollHeight; }")
        page.wait_for_selector("ul.inv")
        page.evaluate("() => { const b = document.getElementById('box'); b.scrollTop = b.scrollHeight; }")
        _dl_first(page)
    page, tab, rec, _ = _record(x, acts, state={"lazy": True})
    click = [s for s in rec["steps"] if s["action"] == "click"][-1]
    assert click.get("innerScroll", 0) > 300, click
    run = _run(x, page, tab, rec, state={"lazy": True})
    assert run["result"]["verdict"] == "VERIFIED", _logs(run)
    assert "/files/doc/invoice-2026-10.pdf" in x.server.hits
    assert not run.get("modelCalls"), run.get("modelCalls")
    assert any("scrolled" in t for t in _logs(run)), _logs(run)


# ---- units: period of a dated row; the app-window rule -------------------------------------------------------------

def test_row_periods_and_app_window_rule(x, server):
    out = x.sw.evaluate("""[PD.rowPeriods('Nov 1, 2026 · $20.00 · Paid'), PD.rowPeriods('1 November 2026'), PD.rowPeriods('2026-11-01'),
                            PD.rowPeriods('INV-2026-09-0042'), PD.rowPeriods('Sept. 30, 2026 receipt')]""")
    assert out == [["2026-11"], ["2026-11"], ["2026-11"], [], ["2026-09"]], out
    page = x.ctx.new_page()
    page.goto(server.base + HOME)
    page.click("#prof")
    page.click("#m-settings")
    tab = x.tab_id(page)                  # the content script lives in an isolated world: ask it like the worker does
    kept = x.sw.evaluate("(t) => chrome.tabs.sendMessage(t, {type: 'unblock', keep: {name: 'Download PDF'}})", tab)
    assert kept["dismissed"] == [] and kept["kept"] and "tabs" in kept["kept"][0]["why"], kept
    shut = x.sw.evaluate("(t) => chrome.tabs.sendMessage(t, {type: 'unblock', keep: {name: 'S Saurabh · Max', covered: true}})", tab)
    assert shut["dismissed"] and shut["dismissed"][0]["name"] == "Close", shut


# ---- v0.9.3: the popup offers "Run for this month" for a recipe that picks its row by date (no templated text) --------

def test_popup_offers_run_for_a_month_when_the_row_is_picked_by_date(x):
    page, tab, rec, _ = _record(x, _full_route)
    assert [s for s in rec["steps"] if s["action"] == "click"][-1]["loc"]["row"]
    pop = x.popup(tab)
    pop.wait_for_selector(".periodbox", state="attached", timeout=5000)
    box = pop.locator(".periodbox").first
    assert "1 row pick follow" in box.inner_text(), box.inner_text()
    assert box.locator("input.prun").count() == 1 and box.locator("button.prungo").count() == 1
    pop.close()
