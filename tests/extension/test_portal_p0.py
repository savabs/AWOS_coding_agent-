"""AWOS Replay 0.9 P0 items (docs/product/portal_runs_plan.md), end to end on local fixture portals:

  1. real file checks: a login / error page saved as invoice.pdf (served as text/html, or mislabelled as a PDF) ->
     Not a document; an empty file -> not Got; a real PDF -> Got with "content checked"; CSV by content; the
     collection checklist and the phone status say Not a document
  2. AI-read unknown pop-ups: a survey pop-up no known rule closes is read by the FAKE agent brain as HARMLESS and
     closed with its "Close survey" control, the run goes on; an "updated terms" pop-up pauses + sends a phone need
     even though the brain says HARMLESS; the model's classification is logged
  3. catch-up: the listing has Aug / Sep / Oct, the collection history has Sep -> Oct and Aug are fetched (each
     verified) and recorded, Sep is not fetched again
  4. wait until usable: a button disabled for 2 s is clicked after the wait; a full-page spinner is waited out
Run:  PYTHONPATH=. ~/.awos/rl-venv/bin/python -m pytest tests/extension -q -k p0
"""
import time

import pytest

from test_phone_link import _crypto_page, _needs, _pair

pytestmark = pytest.mark.timeout(180)


def _wait(fn, timeout=30, what="condition"):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.2)
    raise AssertionError("timed out waiting for " + what)


def _record(x, path, name, actions):
    page = x.ctx.new_page()
    page.goto(x.server.base + path)
    tab = x.tab_id(page)
    x.call("start_recording", tabId=tab, name=name)
    actions(page)
    time.sleep(0.7)
    return page, tab, x.call("stop_recording")


def _click_download(page, sel):
    with page.expect_download():
        page.click(sel)
    time.sleep(0.4)


def _run(x, page, tab, rec):
    page.goto(x.server.base + "/help.html")
    x.call("run", recId=rec["id"], tabId=tab)
    return x.wait_run(statuses=("done", "failed", "needs_user", "awaiting_approval"))


def _dl(run):
    return [c for c in run["result"]["checks"] if c["kind"] == "download"][0]


# ---- 1. real file checks ------------------------------------------------------------------------------------------

def _record_docs(x, sel="#pdf", rx=r"invoice-{{year}}-{{month}}\.pdf$"):
    x.server.state.update(doc="good")
    page, tab, rec = _record(x, "/portal/docs.html", "Billing documents", lambda p: _click_download(p, sel))
    x.call("save_recording", id=rec["id"], expect={"urlPattern": "", "texts": []}, expectDownload={"nameRegex": rx})
    return page, tab, rec


def test_real_pdf_is_got_with_content_checked(x):
    page, tab, rec = _record_docs(x)
    run = _run(x, page, tab, rec)
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    dl = _dl(run)
    assert dl["ok"] and dl["content"]["state"] == "ok" and "%PDF-" in dl["content"]["why"], dl
    assert "/files/doc/invoice-2026-10.pdf" in x.server.peeks                      # the first bytes were read again
    assert x.server.hits.count("/files/doc/invoice-2026-10.pdf") == 2             # recording + replay, the peek not counted


@pytest.mark.parametrize("mode", ["html", "html_as_doc"])
def test_html_error_page_saved_as_pdf_is_not_a_document(x, mode):
    page, tab, rec = _record_docs(x)
    x.server.state.update(doc=mode)
    run = _run(x, page, tab, rec)
    assert run["result"]["verdict"] == "FAILED" and run["result"]["code"] == "NOT_A_DOCUMENT", run["log"]
    dl = _dl(run)
    assert not dl["ok"] and dl["notDocument"] and dl["content"]["state"] == "bad", dl
    assert "web page" in dl["content"]["why"] and "login or error page" in dl["content"]["why"], dl
    assert run["result"]["reason"].startswith("Not a document: "), run["result"]
    assert not run["result"].get("wrongPeriod") and not dl.get("wrongPeriod")


def test_empty_file_is_not_got(x):
    page, tab, rec = _record_docs(x)
    x.server.state.update(doc="empty")
    run = _run(x, page, tab, rec)
    assert run["result"]["code"] == "NOT_A_DOCUMENT", run["log"]
    assert "empty (0 bytes)" in _dl(run)["content"]["why"]


def test_csv_checked_by_content(x):
    page, tab, rec = _record_docs(x, "#csv", r"transactions-{{year}}-{{month}}\.csv$")
    run = _run(x, page, tab, rec)
    assert run["result"]["verdict"] == "VERIFIED" and _dl(run)["content"]["state"] == "ok", run["log"]
    assert "delimiter" in _dl(run)["content"]["why"]
    x.server.state.update(doc="notcsv")
    run = _run(x, page, tab, rec)
    assert run["result"]["code"] == "NOT_A_DOCUMENT" and "no delimiter" in _dl(run)["content"]["why"], run["log"]


def test_classifier_rules(x):
    out = x.sw.evaluate("""[
      AWOSFile.classify({name: 'a.pdf', size: 900, mime: 'application/pdf', head: '%PDF-1.7\\n...'}).state,
      AWOSFile.classify({name: 'a.pdf', size: 900, mime: 'application/pdf', head: '<!DOCTYPE html><html>'}).state,
      AWOSFile.classify({name: 'a.pdf', size: 900, mime: 'text/html'}).state,
      AWOSFile.classify({name: 'a.pdf', size: 0, mime: 'application/pdf'}).state,
      AWOSFile.classify({name: 'a.pdf', size: 900, mime: 'application/pdf', peekWhy: 'the file is on another origin'}).why,
      AWOSFile.classify({name: 'r.xlsx', size: 900, mime: 'application/octet-stream', head: 'PK\\u0003\\u0004'}).state,
      AWOSFile.classify({name: 'r.xlsx', size: 900, mime: 'application/octet-stream', head: 'hello'}).state,
      AWOSFile.classify({name: 't.csv', size: 9, mime: 'text/csv', head: 'a\\tb\\n1\\t2'}).state,
      AWOSFile.classify({name: 't.csv', size: 9, mime: 'text/csv', head: 'PK\\u0003\\u0004\\u0000\\u0000'}).state,
      AWOSFile.classify({name: 'a.pdf', size: 900, mime: 'application/pdf', head: '<html>login', peekType: 'text/html'}).state,
    ]""")
    assert out == ["ok", "bad", "bad", "bad", "content not checked (the file is on another origin)", "ok", "bad", "ok", "bad", "unchecked"], out


def test_not_a_document_in_collection_checklist_and_phone_status(x):
    x.set_settings(phonePollMs=300, phonePushGapMs=0, preflightSpacingMs=0)
    _, keys = _pair(x)
    _, _, rec = _record_docs(x)
    x.server.state.update(doc="html")
    col = x.call("coll_save", name="Month-end", recIds=[rec["id"]])
    x.call("coll_run", id=col["id"], period="2026-10")
    c = _wait(lambda: (lambda c: c if c and c["status"] == "done" else None)(x.state()["coll"]), timeout=60, what="the collection")
    res = x.state()["collResults"][col["id"]]["2026-10"][rec["id"]]
    assert res["status"] == "not_document" and "web page" in res["reason"], res
    assert c["summary"] == "0 of 1 got, 1 not a document", c
    popup = x.popup(x.tab_id(x.ctx.pages[0]))
    popup.wait_for_selector(f"li[data-coll='{col['id']}'] .st.not_document")
    assert "Not a document (invoice-2026-10.pdf)" in popup.inner_text(f"li[data-coll='{col['id']}']")
    cp = _crypto_page(x)
    from conftest import relay_box
    sts = [o for o in (cp.evaluate("([k, d, e]) => window.__open(k, d, e)", [keys["k"], "to-phone", e])
                       for e in relay_box(x.server, keys["c"], "to-phone")) if o["type"] == "status"]
    assert sts and sts[-1]["items"][0]["state"] == "not a document" and "web page" in sts[-1]["items"][0]["detail"], sts


# ---- 2. AI-read pop-ups -------------------------------------------------------------------------------------------

def _record_payouts(x):
    page, tab, rec = _record(x, "/portal/payouts.html", "Payouts", lambda p: _click_download(p, "#oct"))
    x.call("save_recording", id=rec["id"], expect={"urlPattern": "", "texts": []},
           expectDownload={"nameRegex": r"payout-{{year}}-{{month}}\.csv$"})
    return page, tab, rec


class PopupBrain:
    """the FAKE agent brain: answers the pop-up classification call with `answer`, anything else NOT_FOUND."""

    def __init__(self, answer):
        self.answer, self.requests = answer, []

    def __call__(self, body):
        if body["messages"][0]["content"].startswith("You read ONE pop-up"):
            self.requests.append(body)
            return self.answer
        return "NOT_FOUND"


def test_unknown_harmless_popup_is_closed_by_the_brain_and_run_continues(x):
    page, tab, rec = _record_payouts(x)
    brain = PopupBrain("HARMLESS 2 # a survey invitation, closing it changes nothing")
    x.server.model_answer = brain
    x.server.overrides["/portal/payouts.html"] = "portal/payouts_survey.html"
    run = _run(x, page, tab, rec)
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    assert page.evaluate("[window.__closed, window.__started]") == [1, None]
    assert len(brain.requests) == 1
    prompt = brain.requests[0]["messages"][1]["content"]
    assert '<popup untrusted="true">' in prompt and "Quick survey" in prompt and '2. "Close survey"' in prompt, prompt
    p = run["popups"][0]
    assert p["model"] == "HARMLESS 2" and p["verdict"] == "HARMLESS" and p["control"] == "Close survey" and "survey" in p["modelReason"], p
    assert any('closed the pop-up "Quick survey" with "Close survey"' in l["text"] for l in run["log"]), run["log"]
    assert any(m.get("kind") == "popup" for m in run["modelCalls"])


def test_brain_naming_a_forbidden_control_is_not_clicked(x):
    page, tab, rec = _record_payouts(x)
    x.server.model_answer = PopupBrain("HARMLESS 1 # take the survey")          # 1 = "Start survey": not a close word
    x.server.overrides["/portal/payouts.html"] = "portal/payouts_survey.html"
    run = _run(x, page, tab, rec)
    assert run["status"] == "needs_user" and run["pending"]["popup"] == "popup", run["log"]
    assert page.evaluate("[window.__closed, window.__started]") == [None, None]
    assert "not a close / dismiss control" in run["popups"][0]["reason"], run["popups"]


def test_accept_new_terms_always_pauses_and_goes_to_the_phone_even_if_brain_says_harmless(x):
    x.set_settings(phonePollMs=300, phonePushGapMs=0)
    _, keys = _pair(x)
    page, tab, rec = _record_payouts(x)
    brain = PopupBrain("HARMLESS 1 # just a notice")
    x.server.model_answer = brain
    x.server.overrides["/portal/payouts.html"] = "portal/payouts_terms.html"
    run = _run(x, page, tab, rec)
    assert run["status"] == "needs_user" and run["pending"]["decision"]["verdict"] == "NEEDS_DECISION", run["log"]
    assert "a pop-up needs your decision" in run["pending"]["message"] and "Accept and continue" in run["pending"]["message"]
    assert page.evaluate("window.__agreed") is None                            # never clicked
    assert len(brain.requests) == 1
    p = run["popups"][0]
    assert p["model"] == "HARMLESS 1" and p["verdict"] == "NEEDS_DECISION" and p["rule"] == "terms", p
    assert any("overridden" in l["text"] for l in run["log"]), run["log"]
    cp = _crypto_page(x)
    needs = _wait(lambda: _needs(x, cp, keys), what="the phone need")
    assert needs[0]["kind"] == "approve" and "needs your decision" in needs[0]["message"], needs
    # the user reads and closes the terms at the Mac; Continue goes on
    page.evaluate("document.getElementById('ov').remove()")
    x.call("continue", runId=run["id"], nonce=run["pending"]["nonce"])
    run = x.wait_run()
    assert run["result"]["verdict"] == "VERIFIED" and page.evaluate("window.__agreed") is None, run["log"]


def test_decision_rule_and_harmless_vocabulary(x):
    out = x.sw.evaluate("""[
      AWOSPortal.decisionWord({title: 'Updated terms of service', body: 'Please review', controls: ['Accept and continue']}),
      AWOSPortal.decisionWord({title: 'Quick survey', body: 'How was your month-end?', controls: ['Start survey', 'Close survey', 'Cancel']}),
      AWOSPortal.decisionWord({title: 'Your plan', body: 'Upgrade to Pro for more exports', controls: ['Not now']}),
      AWOSPortal.decisionWord({title: 'Share your data with partners?', body: '', controls: ['Later']}),
      AWOSPortal.HARMLESS_RE.test('Close survey'), AWOSPortal.HARMLESS_RE.test('Remind me later'), AWOSPortal.HARMLESS_RE.test('Start survey'),
    ]""")
    assert out == ["terms", "", "upgrade", "share your data", True, True, False], out


# ---- 3. catch up on missed months -----------------------------------------------------------------------------------

def test_catch_up_fetches_missed_months_only(x):
    x.set_settings(preflightSpacingMs=0)
    x.server.state.update(latest="2026-09")
    page, tab, rec = _record(x, "/portal/invoices.html", "Supplier invoices", lambda p: _click_download(p, "text=Invoice September 2026"))
    x.server.state.update(latest="2026-10")                  # the listing now shows October, September, August
    col = x.call("coll_save", name="Month-end", recIds=[rec["id"]])
    assert x.call("coll_save", id=col["id"], catchUpMonths=3)["catchUpMonths"] == 3
    x.sw.evaluate("([c, r]) => setCollResult(c, '2026-09', r, {status: 'got', file: 'invoice-2026-09.pdf'})", [col["id"], rec["id"]])
    x.server.hits.clear()
    x.call("coll_run", id=col["id"], period="2026-10")
    c = _wait(lambda: (lambda c: c if c and c["status"] == "done" else None)(x.state()["coll"]), timeout=90, what="the collection")
    gen = sorted(h for h in x.server.hits if h.startswith("/files/gen/"))
    assert gen == ["/files/gen/invoice-2026-08.pdf", "/files/gen/invoice-2026-10.pdf"], x.server.hits
    res = x.state()["collResults"][col["id"]]
    assert res["2026-10"][rec["id"]]["status"] == "got" and not res["2026-10"][rec["id"]].get("caughtUp")
    aug = res["2026-08"][rec["id"]]
    assert aug["status"] == "got" and aug["caughtUp"] and aug["file"] == "invoice-2026-08.pdf" and aug["content"] == "ok", aug
    assert res["2026-09"][rec["id"]]["file"] == "invoice-2026-09.pdf" and not res["2026-09"][rec["id"]].get("caughtUp")
    assert "2026-07" not in res
    assert "caught up August 2026" in c["summary"], c
    run = x.state()["runs"][0]
    assert any("September 2026 is already got" in l["text"] for l in run["log"]), run["log"]
    assert any("July 2026 is not listed" in l["text"] for l in run["log"]), run["log"]


def test_catch_up_off_switch(x):
    x.set_settings(preflightSpacingMs=0)
    x.server.state.update(latest="2026-09")
    _, _, rec = _record(x, "/portal/invoices.html", "Supplier invoices", lambda p: _click_download(p, "text=Invoice September 2026"))
    x.server.state.update(latest="2026-10")
    col = x.call("coll_save", name="Month-end", recIds=[rec["id"]], catchUp=False)
    x.server.hits.clear()
    x.call("coll_run", id=col["id"], period="2026-10")
    _wait(lambda: (lambda c: c if c and c["status"] == "done" else None)(x.state()["coll"]), timeout=90, what="the collection")
    assert [h for h in x.server.hits if h.startswith("/files/gen/")] == ["/files/gen/invoice-2026-10.pdf"], x.server.hits


# ---- 4. wait until usable -----------------------------------------------------------------------------------------

def _record_slow(x):
    x.server.state.clear()
    page, tab, rec = _record(x, "/portal/slow.html", "Slow payouts", lambda p: _click_download(p, "#dl"))
    x.call("save_recording", id=rec["id"], expect={"urlPattern": "", "texts": []},
           expectDownload={"nameRegex": r"payout-{{year}}-{{month}}\.csv$"})
    return page, tab, rec


def test_disabled_button_is_clicked_after_it_is_enabled(x):
    page, tab, rec = _record_slow(x)
    x.server.state.update(disabledMs=2000)
    run = _run(x, page, tab, rec)
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    assert page.evaluate("window.__clicks") == 1
    assert page.evaluate("window.__clickedAt - window.__loadedAt") >= 1900
    t = [w for w in run["timings"] if w["what"] == "wait_usable"]
    assert t and t[0]["why"] == "disabled" and t[0]["ms"] >= 1000, run.get("timings")
    assert any("to be usable (disabled)" in l["text"] for l in run["log"]), run["log"]


def test_spinner_overlay_is_waited_out(x):
    page, tab, rec = _record_slow(x)
    x.server.state.update(spinnerMs=2000)
    run = _run(x, page, tab, rec)
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    assert not run.get("popups") and not run.get("dismissed"), run      # a loading spinner is not a pop-up
    t = [w for w in run["timings"] if w["what"] == "wait_usable"]
    assert t and "spinner" in t[0]["why"], run.get("timings")


def test_never_usable_falls_through_to_handoff(x):
    page, tab, rec = _record_slow(x)
    x.set_settings(usableWaitMs=600)
    x.server.state.update(disabledMs=600000)
    run = _run(x, page, tab, rec)
    assert run["status"] == "needs_user" and run["pending"]["unusable"] == "disabled", run["log"]
    assert page.evaluate("window.__clicks") == 0
    page.evaluate("document.getElementById('dl').disabled = false")
    x.call("continue", runId=run["id"], nonce=run["pending"]["nonce"])
    run = x.wait_run()
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
