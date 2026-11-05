"""AWOS Replay 0.7 goal-guided runs (docs/specs/goal_guided.md), end to end on a local invoices portal
(tests/extension/pages/portal/invoices.html: which months are posted and the page layout come from server.state).

  - period templating: recorded for 2026-09 ("Invoice September 2026") -> run for 2026-10 downloads the October file
    with ZERO model calls (repair or agent)
  - the portal is redesigned ("Oct-2026 Invoice" buttons): the agent brain (FAKE, scripted micro-agent replies) decides
    the step, October is downloaded, VERIFIED; the update offer is accepted in the popup -> the next run (November)
    has zero model calls
  - November not posted yet: the model says so -> MISSING with its reason, nothing downloaded (server hit counts), and
    the collection checklist says Missing
  - the portal serves last month's file -> WRONG_PERIOD in the verdict and in the checklist (not Got)
  - safety: a model-decided "Delete ... draft" click waits for Approve; nothing is clicked before; no second model call
  - cloud: a Claude agent brain without consent, or with a cloud-locked recipe, never sees the page (the local model
    decides); with consent + key it is used
  - the period detector (many formats, no false positives on order ids, prices, phone numbers, dates) and the recipe
    round trip (placeholders + period travel in a shared recipe and come back as templates)
  - the popup shows the templating ("September 2026" -> "{{month_name}} {{year}}") and undoes it per field
Run:  PYTHONPATH=. ~/.awos/rl-venv/bin/python -m pytest tests/extension -q -k goal_guided
"""
import json
import pathlib
import time

import pytest

from test_tell_it import FAKE_CLAUDE, KEY, Brain

pytestmark = pytest.mark.timeout(180)
EXT = pathlib.Path(__file__).resolve().parents[2] / "extension"


def _wait(fn, timeout=30, what="condition"):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.2)
    raise AssertionError("timed out waiting for " + what)


def _record_september(x, name="Supplier invoices"):
    """record the September invoice download on the portal while September is the newest month posted."""
    x.server.state.clear()
    x.server.state.update(latest="2026-09")
    page = x.ctx.new_page()
    page.goto(x.server.base + "/portal/invoices.html")
    tab = x.tab_id(page)
    x.call("start_recording", tabId=tab, name=name)
    with page.expect_download():
        page.click("text=Invoice September 2026")
    time.sleep(0.9)
    rec = x.call("stop_recording")
    k = next(i for i, s in enumerate(rec["steps"]) if s["action"] == "click")
    return page, tab, rec, k


def _gen_hits(x, prefix="/files/gen/"):
    return [h for h in x.server.hits if h.startswith(prefix)]


def _run(x, page, rec, tab, **kw):
    page.goto(x.server.base + "/help.html")
    x.server.hits.clear()
    x.server.model_calls.clear()          # per run: what THIS run asked (the Brain objects keep their own count)
    x.call("run", recId=rec["id"], tabId=tab, **kw)
    return x.wait_run(statuses=("done", "failed", "awaiting_approval", "needs_user"))


def _agent_calls(x):
    """model calls that went to the agent brain (the fake server answers repair and agent calls on one endpoint)."""
    return [b for b in x.server.model_calls if b["messages"][0]["content"].startswith("You operate a web dashboard")]


# ---- 1. templating: next month with zero model calls ---------------------------------------------------------------

def test_recorded_september_runs_october_by_templating_with_zero_model_calls(x):
    page, tab, rec, k = _record_september(x)
    click = rec["steps"][k]
    assert click.get("variable") is True and click["download"]["name"] == "invoice-2026-09.pdf", click
    assert rec["period"] == "2026-09" and rec["periodMode"] == "previous", rec
    tpl = rec["tpl"]
    assert tpl[f"steps.{k}.loc.name"] == {"template": "Invoice {{month_name}} {{year}}", "example": "Invoice September 2026",
                                         "period": "2026-09", "on": True}, tpl
    assert tpl[f"steps.{k}.loc.href"]["template"] == "/files/gen/invoice-{{year}}-{{month}}.pdf"
    assert tpl[f"steps.{k}.download.name"]["template"] == "invoice-{{year}}-{{month}}.pdf"
    assert not any("EUR" in e["example"] and "{{" in e["template"] for e in tpl.values()), tpl      # prices stay literal
    assert rec["steps"][k]["loc"]["name"] == "Invoice September 2026"                                  # the recording keeps the original
    assert rec["goalProposed"] == "Download the {{month_name}} {{year}} invoice from 127.0.0.1"

    x.server.state.update(latest="2026-10")                  # a month later: October is up
    run = _run(x, page, rec, tab)                            # no period given: the previous month (2026-10, clock pinned)
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    assert run["period"] == "2026-10"
    assert x.server.model_calls == [], x.server.model_calls  # no repair and no agent call
    assert _gen_hits(x) == ["/files/gen/invoice-2026-10.pdf"], x.server.hits
    dl = [c for c in run["result"]["checks"] if c["kind"] == "download"][0]
    assert dl["ok"] and dl["actual"] == "invoice-2026-10.pdf" and "2026-10" in dl["expected"], dl
    assert any("replayed with October 2026" in l["text"] for l in run["log"]), run["log"]
    assert not run.get("guided")


# ---- 2. layout changed: the model decides, the update offer makes the next run fast -------------------------------

def test_redesigned_portal_model_decides_then_accepted_update_makes_next_run_fast(x):
    page, tab, rec, k = _record_september(x)
    x.call("save_recording", id=rec["id"], goal="Download the September 2026 supplier invoice")
    brain = Brain(['click "Oct-2026 Invoice"'])
    x.server.model_answer = brain
    x.server.state.update(latest="2026-10", layout="cards")  # the portal was redesigned: buttons "Oct-2026 Invoice"
    run = _run(x, page, rec, tab)
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    assert len(brain.requests) == 1
    assert _gen_hits(x) == ["/files/gen/invoice-2026-10.pdf"], x.server.hits
    g = run["guided"]
    assert len(g) == 1 and g[0]["control"] == {"role": "button", "name": "Oct-2026 Invoice"} and g[0]["step"] == k, g
    prompt = brain.requests[0]["messages"][1]["content"]
    assert prompt.startswith("Goal: Download the October 2026 supplier invoice\n"), prompt[:200]   # the goal's period substituted
    assert 'Last time at this step you clicked the link "Invoice September 2026"' in prompt
    assert "replayed for October 2026 (it was recorded for September 2026)" in prompt
    assert "1. opened " + x.server.base + "/portal/invoices.html -> ok" in prompt                      # the steps done so far
    assert '<observation untrusted="true">' in prompt and '"Oct-2026 Invoice"' in prompt
    assert any("decided by the model: click(" in l["text"] and '"Oct-2026 Invoice"' in l["text"] for l in run["log"]), run["log"]
    mc = [m for m in run["modelCalls"] if m.get("kind") == "guided"]
    assert len(mc) == 1 and mc[0]["outcome"].startswith("click(") and mc[0]["why"], mc
    offer = run["updateOffer"]
    assert offer["steps"][0]["to"]["name"] == "Oct-2026 Invoice" and 'link "Invoice September 2026"' in offer["steps"][0]["from"]

    popup = x.popup(tab)                                     # "the portal changed: use the new step from now on?"
    popup.wait_for_selector("#updateoffer:not([hidden])")
    assert "The portal changed" in popup.inner_text("#updatemsg") and "Oct-2026 Invoice" in popup.inner_text("#updatemsg")
    popup.click("#updateyes")
    _wait(lambda: x.state()["recordings"][rec["id"]].get("history"), what="the recipe update")
    r2 = x.state()["recordings"][rec["id"]]
    assert r2["steps"][k]["loc"]["name"] == "Oct-2026 Invoice" and r2["steps"][k]["hint"] == 'the button "Oct-2026 Invoice"'
    assert r2["tpl"][f"steps.{k}.loc.name"]["template"] == "{{month_short}}-{{year}} Invoice", r2["tpl"]
    h = r2["history"][-1]
    assert h["accepted"] and h["period"] == "2026-10" and 'link "Invoice September 2026"' in h["from"] and "Oct-2026 Invoice" in h["to"], h
    popup.wait_for_selector("#updateoffer[hidden]", state="attached")
    assert x.state()["run"]["updateOffer"]["done"] == "accepted"

    x.server.state.update(latest="2026-11", layout="cards")  # next month, same new layout: fast again
    run = _run(x, page, rec, tab, period="2026-11")
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    assert len(brain.requests) == 1 and _agent_calls(x) == [] and x.server.model_calls == []
    assert _gen_hits(x) == ["/files/gen/invoice-2026-11.pdf"], x.server.hits
    assert not run.get("updateOffer")


# ---- 3. not posted yet: MISSING, nothing downloaded ----------------------------------------------------------------

def test_november_not_posted_is_missing_with_the_models_reason_and_nothing_downloaded(x):
    page, tab, rec, k = _record_september(x)
    brain = Brain(['needs_user "No November 2026 invoice is listed yet; the newest is October 2026"'] * 2)
    x.server.model_answer = brain
    x.server.state.update(latest="2026-10")
    run = _run(x, page, rec, tab, period="2026-11")
    assert run["status"] == "failed" and run["result"]["verdict"] == "MISSING", run["log"]
    assert "No November 2026 invoice is listed yet" in run["result"]["reason"], run["result"]
    assert "the recorded target was not used" in run["result"]["reason"]
    assert _gen_hits(x) == [], x.server.hits                  # neither October nor September was downloaded
    assert len(brain.requests) == 1
    # in a collection the checklist says Missing (with the reason), not Failed
    col = x.call("coll_save", name="Month-end supplier docs", recIds=[rec["id"]])
    x.server.hits.clear()
    x.call("coll_run", id=col["id"], period="2026-11")
    c = _wait(lambda: (lambda c: c if c and c["status"] == "done" else None)(x.state()["coll"]), timeout=60, what="the collection")
    res = x.state()["collResults"][col["id"]]["2026-11"][rec["id"]]
    assert res["status"] == "missing" and "No November 2026 invoice" in res["reason"], res
    assert c["summary"] == "0 of 1 got, 1 missing", c
    assert _gen_hits(x) == [], x.server.hits


# ---- 4. the portal serves last month's file: WRONG PERIOD ----------------------------------------------------------

def test_file_for_the_old_period_is_wrong_period_not_got(x):
    page, tab, rec, k = _record_september(x)
    x.server.state.update(latest="2026-10", stale=True)       # "Invoice October 2026" downloads September's file
    run = _run(x, page, rec, tab)
    assert run["result"]["verdict"] == "WRONG_PERIOD", run["log"]
    assert "invoice-2026-09.pdf" in run["result"]["reason"] and "not October 2026" in run["result"]["reason"], run["result"]
    dl = [c for c in run["result"]["checks"] if c["kind"] == "download"][0]
    assert dl["wrongPeriod"] and not dl["ok"] and dl["actual"] == "invoice-2026-09.pdf", dl
    assert x.server.model_calls == []
    col = x.call("coll_save", name="Month-end", recIds=[rec["id"]])
    x.call("coll_run", id=col["id"], period="2026-10")
    c = _wait(lambda: (lambda c: c if c and c["status"] == "done" else None)(x.state()["coll"]), timeout=60, what="the collection")
    res = x.state()["collResults"][col["id"]]["2026-10"][rec["id"]]
    assert res["status"] == "wrong_period" and res["file"].startswith("invoice-2026-09"), res
    assert c["summary"] == "0 of 1 got, 1 wrong period", c
    popup = x.popup(tab)
    popup.wait_for_selector(f"li[data-coll='{col['id']}'] .st.wrong_period")
    assert "Wrong period (invoice-2026-09" in popup.inner_text(f"li[data-coll='{col['id']}']")


# ---- 5. safety gates apply to model-decided steps ------------------------------------------------------------------

def test_model_decided_delete_waits_for_approval(x):
    page, tab, rec, k = _record_september(x)
    brain = Brain(['click "Delete Oct-2026 draft"', 'click "Oct-2026 Invoice"'])
    x.server.model_answer = brain
    x.server.state.update(latest="2026-10", layout="cards", drafts=True)
    run = _run(x, page, rec, tab)
    assert run["status"] == "awaiting_approval", run["log"]
    assert run["pending"]["target"] == "Delete Oct-2026 draft" and run["pending"]["guided"] and run["pending"]["danger"], run["pending"]
    time.sleep(1.0)
    assert page.evaluate("window.__deleted") is None           # nothing clicked before Approve
    assert len(brain.requests) == 1
    x.call("approve", runId=run["id"], nonce=run["pending"]["nonce"])
    run = x.wait_run()
    assert page.evaluate("window.__deleted") == 1
    assert len(brain.requests) == 1                            # the approved decision ran; the model was not asked again
    assert run["guided"][0]["approved"] is True and run["guided"][0]["control"]["name"] == "Delete Oct-2026 draft"
    assert run["result"]["verdict"] == "FAILED" and _gen_hits(x) == []      # it deleted a draft, it did not download: not done


# ---- 6. the cloud: never without consent, never for a locked recipe ------------------------------------------------

def test_cloud_agent_only_with_consent_and_recipe_lock_wins(x):
    page, tab, rec, k = _record_september(x)
    x.sw.evaluate(FAKE_CLAUDE, ['click(1)'])
    brain = Brain(['click "Oct-2026 Invoice"'] * 3)
    x.server.model_answer = brain
    x.server.state.update(latest="2026-10", layout="cards")
    x.call("save_claude_key", key=KEY)
    x.set_settings(agentBrain="claude")                         # no consent ticked
    run = _run(x, page, rec, tab)
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    assert x.sw.evaluate("self.__claudeCalls.length") == 0 and len(brain.requests) == 1
    assert any("uses the local agent model (Claude brain not enabled" in l["text"] for l in run["log"]), run["log"]
    x.set_settings(agentBrain="claude", cloudConsent=True)
    x.call("set_recipe_cloud_lock", id=rec["id"], on=True)    # consent, key, but this recipe never goes to the cloud
    x.call("heal_revert", id=rec["id"], step=k)               # v0.10: else the learned fix finds it with no model call
    run = _run(x, page, rec, tab)
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    assert x.sw.evaluate("self.__claudeCalls.length") == 0 and len(brain.requests) == 2
    assert any("cloud is locked off for this recipe" in l["text"] for l in run["log"]), run["log"]
    x.call("set_recipe_cloud_lock", id=rec["id"], on=False)
    x.call("heal_revert", id=rec["id"], step=k)
    run = _run(x, page, rec, tab)
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    calls = [c for c in x.sw.evaluate("self.__claudeCalls") if c["body"]["system"].startswith("You operate")]
    assert len(calls) == 1 and len(brain.requests) == 2 and calls[0]["headers"]["x-api-key"] == KEY
    assert "Last time at this step you clicked" in calls[0]["body"]["messages"][0]["content"]


# ---- 7. the popup: templating shown, undo per field, the month a run uses -------------------------------------------

def test_popup_shows_templating_and_undo_per_field(x):
    page, tab, rec, k = _record_september(x)
    popup = x.popup(tab)
    li = f"li[data-rec='{rec['id']}']"
    popup.wait_for_selector(li + " details.periodbox")
    popup.click(li + " details.periodbox summary")
    row = f"{li} .tplrow[data-path='steps.{k}.loc.name']"
    txt = popup.inner_text(row)
    assert '"Invoice September 2026" → "Invoice {{month_name}} {{year}}"' in txt, txt
    assert "recorded for September 2026" in popup.inner_text(li + " details.periodbox summary")
    popup.click(row + " button.tplundo")
    _wait(lambda: x.state()["recordings"][rec["id"]]["tpl"][f"steps.{k}.loc.name"]["on"] is False, what="the undo")
    popup.wait_for_selector(row + ".off")
    assert popup.inner_text(row + " button.tplundo") == "Redo"
    v = x.sw.evaluate("(id) => getRec(id).then(r => AWOSPeriod.view(r, '2026-10'))", rec["id"])
    assert v["steps"][k]["loc"]["name"] == "Invoice September 2026"            # undone: stays as recorded
    assert v["steps"][k]["loc"]["href"] == "/files/gen/invoice-2026-10.pdf"     # the other fields still follow the month
    popup.select_option(li + " select.pmode", "this")
    _wait(lambda: x.state()["recordings"][rec["id"]]["periodMode"] == "this", what="the period mode")
    assert x.sw.evaluate("(id) => getRec(id).then(defaultPeriod)", rec["id"]) == "2026-11"


# ---- 8. the detector, without the extension --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def lib(pw):
    try:
        b = pw.chromium.launch(headless=True)
    except Exception as e:
        pytest.skip(f"no Chromium: {e}")
    p = b.new_page()
    p.set_content("<html><body></body></html>")
    for f in ("lib/safety.js", "lib/period.js", "lib/recipe.js"):
        p.add_script_tag(path=str(EXT / f))
    yield p
    b.close()


DETECT = [   # text, kind, recording period, template
    ("Invoice September 2026", "text", "2026-09", "Invoice {{month_name}} {{year}}"),
    ("Statement for SEPTEMBER 2026", "text", "2026-09", "Statement for {{month_name_upper}} {{year}}"),
    ("Sep 2026", "text", "2026-09", "{{month_short}} {{year}}"),
    ("Sept. 2026 payout", "text", "2026-09", "{{month_short}}. {{year}} payout"),
    ("Oct-2026 Invoice", "text", "2026-10", "{{month_short}}-{{year}} Invoice"),
    ("2026 September report", "text", "2026-09", "{{year}} {{month_name}} report"),
    ("/files/invoice-2026-09.pdf", "url", "2026-09", "/files/invoice-{{year}}-{{month}}.pdf"),
    ("https://portal.example/statements/2026/09/?period=2026-09", "url", "2026-09",
     "https://portal.example/statements/{{year}}/{{month}}/?period={{year}}-{{month}}"),
    ("inv-sep-2026.pdf", "text", "2026-09", "inv-{{month_short_lower}}-{{year}}.pdf"),
    ("payout_2026_09.csv", "text", "2026-09", "payout_{{year}}_{{month}}.csv"),
    ("Abrechnung 09.2026", "text", "2026-09", "Abrechnung {{month}}.{{year}}"),
    ("statement-09-2026.pdf", "text", "2026-09", "statement-{{month}}-{{year}}.pdf"),
    ("Period 9/2026", "text", "2026-09", "Period {{month_n}}/{{year}}"),
    ("Q3 2026 VAT return", "text", "2026-08", "Q{{quarter}} {{year}} VAT return"),
    ("vat-2026-Q3.pdf", "text", "2026-07", "vat-{{year}}-Q{{quarter}}.pdf"),
    ("Download September payouts", "text", "2026-09", "Download {{month_name}} payouts"),
    ("invoice-2026-09.pdf", "text", "2026-08", "invoice-2026-09.pdf"),          # another month: stays literal
    ("Statement March 2026", "text", "2026-09", "Statement March 2026"),        # an older statement clicked on purpose
]
NOT_PERIODS = ["Order 2026-0912", "INV-2026-09-0042", "Order #20260915", "EUR 1,209.20", "$12.2026", "Total 2,026.09",
               "Call +1 555-09-2026", "(555) 409-2026", "+49 30 12345678", "Due 2026-09-30", "Paid 15/09/2026",
               "30.09.2026", "Annual report 2026", "v1.2026", "May I help you?", "March forward", "Ref 0912-2026-77",
               "2026-13", "13/2026", "Invoice #2026"]


def test_period_detector_formats(lib):
    out = lib.evaluate("(cases) => cases.map(c => AWOSPeriod.detect(c[0], c[2], c[1]).template)", DETECT)
    assert out == [c[3] for c in DETECT], [(c[0], o) for c, o in zip(DETECT, out) if o != c[3]]
    subs = lib.evaluate("""() => ['Invoice {{month_name}} {{year}}', '{{month_short_lower}}-{{year}}', 'Q{{quarter}} {{year}}',
                                  '{{month}}/{{year}} {{month_n}} {{month_name_upper}}', '{{customer}} {{year}}']
                                 .map(t => AWOSPeriod.substitute(t, '2027-01'))""")
    assert subs == ["Invoice January 2027", "jan-2027", "Q1 2027", "01/2027 1 JANUARY", "{{customer}} 2027"]


def test_period_detector_no_false_positives(lib):
    hits = lib.evaluate("(xs) => xs.map(t => AWOSPeriod.periodsIn(t).map(h => h.text))", NOT_PERIODS)
    bad = [(t, h) for t, h in zip(NOT_PERIODS, hits) if h]
    assert not bad, bad
    # and nothing in them is templated for any period
    tpl = lib.evaluate("(xs) => xs.map(t => AWOSPeriod.detect(t, '2026-09').changed)", NOT_PERIODS)
    assert not any(tpl)


def test_infer_scan_view_consistent(lib):
    r = lib.evaluate("""() => {
      const P = AWOSPeriod, now = new Date(2026, 9, 3).getTime();      // recorded 3 October 2026
      const rec = {goal: '', steps: [{action: 'goto', url: 'https://portal.example/invoices'},
        {action: 'click', loc: {role: 'link', name: 'Invoice September 2026', href: '/files/invoice-2026-09.pdf', block: 'EUR 1,240.00'},
         download: {name: 'invoice-2026-09.pdf'}}], expect: {texts: ['Download of September 2026 started'], urlPattern: ''}};
      const inf = P.infer(rec, now);
      rec.period = inf.period; rec.tpl = P.scan(rec, rec.period);
      const v = P.view(rec, '2026-10'), keep = JSON.stringify(rec.tpl);
      rec.tpl['steps.1.loc.name'].on = false;
      const again = P.scan(rec, rec.period);                          // a re-scan keeps the user's undo
      const older = {steps: [{action: 'click', loc: {name: 'Statement March 2026'}}, {action: 'click', loc: {name: 'Statement April 2026'}}]};
      return {inf, v: [v.steps[1].loc.name, v.steps[1].loc.href, v.steps[1].download.name, v.expect.texts[0], v.steps[1].loc.block],
              kept: again['steps.1.loc.name'].on, olderInf: P.infer(older, now), keys: Object.keys(JSON.parse(keep)).sort(),
              ok: P.consistent(rec.tpl, 1, {name: 'Invoice October 2026', href: '/files/invoice-2026-10.pdf'}, '2026-10'),
              stale: P.consistent(Object.assign({}, rec.tpl, {'steps.1.loc.name': Object.assign({}, rec.tpl['steps.1.loc.name'], {on: true})}),
                                  1, {name: 'Invoice October 2026', href: '/files/invoice-2026-09.pdf'}, '2026-10'),
              old: P.consistent(Object.assign({}, rec.tpl, {'steps.1.loc.name': Object.assign({}, rec.tpl['steps.1.loc.name'], {on: true})}),
                                1, {name: 'Invoice September 2026', href: '/files/invoice-2026-09.pdf'}, '2026-10')};
    }""")
    assert r["inf"] == {"period": "2026-09", "inferred": True, "mode": "previous"}, r["inf"]
    assert r["v"] == ["Invoice October 2026", "/files/invoice-2026-10.pdf", "invoice-2026-10.pdf", "Download of October 2026 started", "EUR 1,240.00"]
    assert r["kept"] is False
    assert r["olderInf"]["inferred"] is False and r["olderInf"]["period"] == "2026-09"      # Mar / Apr are too old to be "the" month
    assert r["keys"] == ["expect.texts.0", "steps.1.download.name", "steps.1.loc.href", "steps.1.loc.name"]
    assert r["ok"]["ok"] is True
    assert r["stale"]["ok"] is False and r["stale"]["bad"] == 1      # the link says October, its file is September's
    assert r["old"]["ok"] is False and r["old"]["bad"] == 2


def test_recipe_round_trip_keeps_placeholders(lib):
    r = lib.evaluate("""() => {
      const P = AWOSPeriod, R = AWOSRecipe;
      const rec = {id: 'rec-1', name: 'Supplier invoices', goal: 'Download the September 2026 invoice', startUrl: 'https://portal.example/invoices?s=1',
        steps: [{action: 'goto', url: 'https://portal.example/invoices'},
                {action: 'fill', loc: {role: 'textbox', name: 'Month'}, value: 'September 2026', sensitive: null},
                {action: 'click', loc: {role: 'link', name: 'Invoice September 2026', href: '/files/invoice-2026-09.pdf'}, variable: true,
                 download: {name: 'invoice-2026-09.pdf'}}],
        expect: {urlPattern: '', texts: ['Invoice September 2026']}, expectDownload: {nameRegex: 'invoice-2026-09\\\\.pdf$'},
        params: [], period: '2026-09', periodMode: 'previous'};
      rec.tpl = P.scan(rec, '2026-09');
      const out = R.fromRecording(rec), back = R.toRecording(out.recipe, 'rec-2');
      const bad = R.validate(Object.assign({}, out.recipe, {checks: Object.assign({}, out.recipe.checks, {goal: 'for {{customer}}'})}));
      const lit = R.validate(Object.assign({}, out.recipe, {steps: out.recipe.steps.map(s => s.action === 'fill' ? Object.assign({}, s, {value: 'September 2026'}) : s)}));
      return {recipe: out.recipe, back: {period: back.period, mode: back.periodMode, name: back.steps[2].loc.name, fill: back.steps[1].value,
              tplName: back.tpl['steps.2.loc.name'], variable: back.steps[2].variable, goal: back.goal,
              view: P.view(back, '2026-12').steps[2].loc.name, rx: P.view(back, '2026-12').expectDownload.nameRegex}, bad: bad.errors, lit: lit.errors};
    }""")
    rc = r["recipe"]
    assert rc["period"] == {"recorded": "2026-09", "mode": "previous"}
    assert rc["checks"]["goal"] == "Download the {{month_name}} {{year}} invoice"
    assert rc["checks"]["expect_text"] == ["Invoice {{month_name}} {{year}}"]
    assert rc["checks"]["expect_download"] == {"name_regex": "invoice-{{year}}-{{month}}\\.pdf$"}
    fill, click = rc["steps"][1], rc["steps"][2]
    assert fill["value"] == "{{month_name}} {{year}}" and "ask" not in fill          # a period fill travels as placeholders
    assert click["loc"]["name"] == "Invoice {{month_name}} {{year}}" and click["variable"] is True
    assert "2026-09" not in json.dumps(rc["steps"]) and "September" not in json.dumps(rc)
    b = r["back"]
    assert b["period"] == "2026-09" and b["mode"] == "previous" and b["variable"] is True
    assert b["name"] == "Invoice September 2026" and b["fill"] == "September 2026" and b["goal"] == "Download the September 2026 invoice"
    assert b["tplName"] == {"template": "Invoice {{month_name}} {{year}}", "example": "Invoice September 2026", "period": "2026-09", "on": True}
    assert b["view"] == "Invoice December 2026" and b["rx"] == "invoice-2026-12\\.pdf$"
    assert any("undeclared param {{customer}}" in e for e in r["bad"]), r["bad"]
    assert any("never carries a literal value" in e for e in r["lit"]), r["lit"]
