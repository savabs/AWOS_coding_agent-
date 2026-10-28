"""AWOS Replay 0.4 end to end in headless Chromium with the unpacked extension.

  - params: a typed field becomes {{name}}, asked at run time (popup prefills the last value), substituted in checks
  - export / share: the recipe from a real recording has no typed values; the share link decodes to it
  - import from the recipe page: hello/present handshake, nothing imported until the user clicks Import in the popup,
    the page then gets awos-import-result ok (or "cancelled"); other origins get no bridge
  - imported recipes: approval gates kept, other sites pause for a domain approval that is remembered
  - schedules: alarm armed for daily / weekly, a scheduled run opens its own tab, records the verdict, notifies;
    a missing param / approval gate pauses + notifies instead of guessing; a busy extension skips with a record
  - downloads: expect_download checked with chrome.downloads (pass and fail)
  - brain: Claude only with consent + key (fake fetch, no real API calls), the key never leaves storage
Run:  PYTHONPATH=. ~/.awos/rl-venv/bin/python -m pytest tests/extension -q
"""
import json
import time

import pytest

from test_extension_replay import _record_checkout, _record_form

pytestmark = pytest.mark.timeout(150)
SITE = "https://awos.tirramind.com"
KEY = "sk-ant-test-0123456789abcdef"

SITE_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Recipe</title></head><body>
<h1>Recipe</h1><script>
window.__msgs = [];
window.addEventListener('message', function (e) { if (e.data && e.data.type && e.data.type.indexOf('awos-') === 0
  && e.data.type !== 'awos-replay-hello' && e.data.type !== 'awos-import-recipe') window.__msgs.push(e.data); });
window.postMessage({type: 'awos-replay-hello'}, location.origin);
</script></body></html>"""


def _msgs(page, typ):
    return [m for m in page.evaluate("window.__msgs") if m["type"] == typ]


def _wait(fn, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.1)
    raise AssertionError("timed out")


def _site(x, origin=SITE):
    x.ctx.route(origin + "/r/**", lambda route: route.fulfill(status=200, content_type="text/html", body=SITE_HTML))
    p = x.ctx.new_page()
    p.goto(origin + "/r/")
    return p


def _err(x, typ, **kw):
    return x.sw.evaluate("([t, m]) => HANDLERS[t](m, {}).then(() => '', e => String(e.message || e))", [typ, kw])


def _raw(x, typ, **kw):
    """a handler's reply as-is (replies that carry an `error` field on purpose)."""
    return x.sw.evaluate("([t, m]) => HANDLERS[t](m, {})", [typ, kw])


def _import(x, recipe):
    r = x.call("import_offer", recipe=recipe)
    assert r["ok"], r
    return x.call("import_confirm", nonce=x.state()["pendingImport"]["nonce"])


# ---- params ---------------------------------------------------------------------------------------------------

def test_param_asked_at_run_time_and_used_in_checks(x):
    page = x.ctx.new_page()
    rec, tab = _record_form(x, page)
    rec = x.call("set_param", id=rec["id"], step=1, param={"name": "fullname", "label": "Whose profile", "example": "Grace Hopper"})
    assert rec["steps"][1]["param"] == "fullname" and rec["params"][0]["label"] == "Whose profile"
    x.call("save_recording", id=rec["id"], expect={"urlPattern": rec["expect"]["urlPattern"],
                                                   "texts": ["Profile saved for {{fullname}} <ada@example.com>"]})
    page.goto(x.server.base + "/help.html")
    x.call("run", recId=rec["id"], tabId=tab, params={"fullname": "Grace Hopper"})
    run = x.wait_run()
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    assert "Profile saved for Grace Hopper" in page.inner_text("#msg")
    assert x.state()["recordings"][rec["id"]]["lastParams"] == {"fullname": "Grace Hopper"}

    # the popup asks again, prefilled with the last value
    page.goto(x.server.base + "/help.html")
    popup = x.popup(tab)
    popup.click(f"li[data-rec='{rec['id']}'] button.run")
    inp = popup.wait_for_selector("input.pval[data-param='fullname']")
    assert inp.input_value() == "Grace Hopper" and inp.get_attribute("placeholder") == "Grace Hopper"
    inp.fill("Katherine Johnson")
    popup.click("button.rungo")
    run = x.wait_run()
    assert run["result"]["verdict"] == "VERIFIED" and "Katherine Johnson" in page.inner_text("#msg"), run["log"]


def test_secret_field_cannot_become_param(x):
    page = x.ctx.new_page()
    page.goto(x.server.base + "/login.html")
    tab = x.tab_id(page)
    x.call("start_recording", tabId=tab, name="login")
    page.fill("#user", "ada")
    page.fill("#pw", "hunter2-SECRET")
    time.sleep(0.5)
    rec = x.call("stop_recording")
    k = next(i for i, s in enumerate(rec["steps"]) if s.get("sensitive"))
    assert "always typed by you" in _err(x, "set_param", id=rec["id"], step=k, param={"name": "pw", "label": "pw"})
    assert "param name" in _err(x, "set_param", id=rec["id"], step=1, param={"name": "Bad Name", "label": "x"})


# ---- export / share --------------------------------------------------------------------------------------------

def test_export_from_real_recording_has_no_typed_values_and_link_decodes(x):
    page = x.ctx.new_page()
    rec, _ = _record_form(x, page)
    x.call("set_param", id=rec["id"], step=1, param={"name": "fullname", "label": "Whose profile", "example": "Grace Hopper"})
    x.call("save_recording", id=rec["id"], goal="Profile saved for Ada Lovelace", expect=rec["expect"])
    out = x.call("export_recipe", id=rec["id"])
    blob = json.dumps(out["recipe"])
    assert "Ada Lovelace" not in blob and "ada@example.com" not in blob and "?" not in blob
    fills = [s for s in out["recipe"]["steps"] if s["action"] == "fill"]
    assert fills[0]["value"] == "{{fullname}}" and fills[1] == {k: fills[1][k] for k in ("action", "loc", "url", "ask")}
    assert out["recipe"]["checks"]["goal"] == "Profile saved for {{fullname}}"
    assert out["recipe"]["checks"]["expect_text"] == [] and out["warnings"]      # it contained the typed e-mail
    assert out["filename"] == "profile.awos.json"
    assert out["link"].startswith(SITE + "/r/#")
    back = x.sw.evaluate("(l) => AWOSRecipe.decodeLink(l)", out["link"])
    assert back["ok"] and back["recipe"] == out["recipe"]


# ---- import from the recipe page ----------------------------------------------------------------------------------

def test_page_import_needs_confirmation_then_answers_page(x):
    page = x.ctx.new_page()
    rec, tab = _record_checkout(x, page)
    recipe = x.call("export_recipe", id=rec["id"])["recipe"]
    site = _site(x)
    assert _wait(lambda: _msgs(site, "awos-replay-present"))[0]["version"] == "0.4.0"
    assert site.evaluate("document.documentElement.dataset.awosReplay") == "0.4.0"

    site.evaluate("(r) => window.postMessage({type: 'awos-import-recipe', recipe: r}, location.origin)", recipe)
    pend = _wait(lambda: x.state()["pendingImport"])
    assert pend["recipe"]["name"] == "pay" and pend["source"].startswith("page " + SITE)
    time.sleep(0.5)
    assert len(x.state()["recordings"]) == 1 and _msgs(site, "awos-import-result") == []    # nothing imported yet

    popup = x.popup(tab)
    popup.wait_for_selector("#importpanel:not([hidden])")
    steps = popup.inner_text("#imp-steps")
    assert 'click "Pay now"' in steps and "you type it" in steps and "open " + x.server.base + "/cart.html" in steps
    assert popup.inner_text("#imp-site") == "127.0.0.1"
    popup.click("#imp-ok")
    assert _wait(lambda: _msgs(site, "awos-import-result")) == [{"type": "awos-import-result", "ok": True}]
    recs = x.state()["recordings"]
    imp = next(r for r in recs.values() if r.get("imported"))
    assert imp["site"] == "127.0.0.1" and x.state()["pendingImport"] is None

    # the imported recipe keeps the approval gate (and asks for the typed note itself)
    page.goto(x.server.base + "/help.html")
    x.call("run", recId=imp["id"], tabId=tab)
    run = x.wait_run(statuses=("needs_user", "awaiting_approval", "done", "failed"))
    assert run["status"] == "needs_user", run["log"]
    x.call("continue", runId=run["id"], nonce=run["pending"]["nonce"])
    run = x.wait_run(statuses=("awaiting_approval", "done", "failed"))
    assert run["status"] == "awaiting_approval" and "Pay now" in run["pending"]["message"]
    assert page.inner_text("#status") == "Not paid"
    x.call("reject", runId=run["id"], nonce=run["pending"]["nonce"])

    # discard -> "cancelled"; an invalid recipe -> an immediate error
    site.evaluate("() => { window.__msgs = []; }")
    site.evaluate("(r) => window.postMessage({type: 'awos-import-recipe', recipe: r}, location.origin)", recipe)
    _wait(lambda: x.state()["pendingImport"])
    popup.wait_for_selector("#importpanel:not([hidden])")
    popup.click("#imp-no")
    assert _wait(lambda: _msgs(site, "awos-import-result")) == [{"type": "awos-import-result", "ok": False, "error": "cancelled"}]
    site.evaluate("() => { window.__msgs = []; }")
    site.evaluate("(r) => window.postMessage({type: 'awos-import-recipe', recipe: Object.assign({}, r, {awos_recipe: 9})}, location.origin)", recipe)
    res = _wait(lambda: _msgs(site, "awos-import-result"))[0]
    assert res["ok"] is False and "awos_recipe" in res["error"]
    assert x.state()["pendingImport"] is None and len(x.state()["recordings"]) == 2


def test_other_origins_get_no_bridge_and_page_senders_are_checked(x):
    evil = _site(x, "https://evil.example")
    time.sleep(1.0)
    assert _msgs(evil, "awos-replay-present") == [] and evil.evaluate("document.documentElement.dataset.awosReplay") is None
    good = {"awos_recipe": 1, "name": "n", "start_url": "https://example.com/", "params": [],
            "steps": [{"action": "goto", "url": "https://example.com/"}], "checks": {}}
    r = x.sw.evaluate("(r) => HANDLERS.import_offer({recipe: r}, {tab: {id: 1}, url: 'https://evil.example/r/'})"
                      ".then(() => 'ok', e => String(e.message))", good)
    assert "only accepted from the AWOS recipe page" in r
    r = x.sw.evaluate("(r) => HANDLERS.import_offer({recipe: r}, {tab: {id: 1}, url: 'https://awos.tirramind.com/r/#x'})", good)
    assert r["ok"] and r["pending"]
    # import_confirm / run are extension-page-only messages
    assert x.sw.evaluate("PAGE_ONLY.import_confirm") is None and x.sw.evaluate("PAGE_ONLY.run") is None
    assert "stale" in _err(x, "import_confirm", nonce="guess")


def test_file_and_link_import_validate(x):
    good = {"awos_recipe": 1, "name": "n", "start_url": "https://example.com/", "params": [],
            "steps": [{"action": "goto", "url": "https://example.com/"}], "checks": {}}
    assert x.call("import_offer", text=json.dumps(good))["ok"]
    assert not _raw(x, "import_offer", text="{not json")["ok"]
    assert not _raw(x, "import_offer", text=json.dumps(dict(good, steps=[{"action": "fill", "loc": {}, "value": "secret"}])))["ok"]
    link = x.sw.evaluate("(r) => AWOSRecipe.encodeLink(r)", good)
    assert x.call("import_offer", link=link)["ok"]
    assert not _raw(x, "import_offer", link=SITE + "/r/#garbage!!")["ok"]
    assert len(x.state()["recordings"]) == 0                                       # offers alone import nothing
    x.call("import_discard")
    popup = x.popup(0)                                                             # the popup's paste-a-link path
    popup.fill("#implink", SITE + "/r/#garbage!!")
    popup.click("#implinkgo")
    popup.wait_for_function("document.getElementById('error').textContent.indexOf('Not a valid recipe') === 0")
    popup.fill("#implink", link)
    popup.click("#implinkgo")
    popup.wait_for_selector("#importpanel:not([hidden])")
    assert popup.inner_text("#imp-start") == "https://example.com/"
    popup.click("#imp-ok")
    _wait(lambda: len(x.state()["recordings"]) == 1)


def test_imported_recipe_is_domain_locked(x):
    base = x.server.base
    other = base.replace("127.0.0.1", "localhost")
    recipe = {"awos_recipe": 1, "name": "hop", "start_url": base + "/form.html", "params": [],
              "steps": [{"action": "goto", "url": base + "/form.html"},
                        {"action": "click", "loc": {"role": "link", "name": "Help", "tag": "a", "href": "/help.html"}},
                        {"action": "goto", "url": other + "/help.html"}],
              "checks": {}}
    rec = _import(x, recipe)
    assert rec["site"] == "127.0.0.1"
    page = x.ctx.new_page()
    page.goto(base + "/cancelled.html")
    tab = x.tab_id(page)
    x.call("run", recId=rec["id"], tabId=tab)
    run = x.wait_run(statuses=("awaiting_approval", "done", "failed"))
    assert run["status"] == "awaiting_approval" and run["pending"]["domain"] == "localhost", run["log"]
    assert "outside its own site 127.0.0.1" in run["pending"]["message"]
    time.sleep(1.0)
    assert page.url.startswith(base + "/help.html")                                # never opened the other site
    x.call("approve", runId=run["id"], nonce=run["pending"]["nonce"])
    run = x.wait_run()
    assert run["status"] == "done" and page.url.startswith(other), run["log"]
    assert x.state()["recordings"][rec["id"]]["approvedDomains"] == ["localhost"]
    page.goto(base + "/cancelled.html")
    x.call("run", recId=rec["id"], tabId=tab)                                    # approved once = remembered
    run = x.wait_run(statuses=("awaiting_approval", "done", "failed"))
    assert run["status"] == "done", run["log"]


# ---- schedules ------------------------------------------------------------------------------------------------

def test_schedule_arms_alarms(x):
    page = x.ctx.new_page()
    rec, _ = _record_form(x, page)
    r = x.call("set_schedule", id=rec["id"], schedule={"kind": "daily", "time": "07:30"})
    got = x.sw.evaluate("""(id) => chrome.alarms.get('awos-sched:' + id).then(a => { var d = new Date(a.scheduledTime);
        return [d.getHours(), d.getMinutes(), a.scheduledTime > Date.now(), a.scheduledTime - Date.now() <= 86400000 + 60000]; })""", rec["id"])
    assert got == [7, 30, True, True] and r["schedule"] == {"kind": "daily", "time": "07:30"}
    x.call("set_schedule", id=rec["id"], schedule={"kind": "weekly", "time": "18:05", "day": 3})
    got = x.sw.evaluate("""(id) => chrome.alarms.get('awos-sched:' + id).then(a => { var d = new Date(a.scheduledTime);
        return [d.getDay(), d.getHours(), d.getMinutes(), a.scheduledTime - Date.now() <= 7 * 86400000 + 60000]; })""", rec["id"])
    assert got == [3, 18, 5, True]
    assert "HH:MM" in _err(x, "set_schedule", id=rec["id"], schedule={"kind": "daily", "time": "25:00"})
    assert "daily or weekly" in _err(x, "set_schedule", id=rec["id"], schedule={"kind": "hourly", "time": "01:00"})
    x.call("set_schedule", id=rec["id"], schedule=None)
    assert x.sw.evaluate("(id) => chrome.alarms.get('awos-sched:' + id).then(a => !!a)", rec["id"]) is False


def _fire(x, rec_id):
    x.sw.evaluate("(id) => fireSchedule(id).then(r => r && r.id)", rec_id)


def test_scheduled_run_opens_own_tab_verifies_and_notifies(x):
    page = x.ctx.new_page()
    rec, tab = _record_form(x, page)
    x.call("set_schedule", id=rec["id"], schedule={"kind": "daily", "time": "06:00"})
    _fire(x, rec["id"])
    run = x.wait_run()
    assert run["scheduled"] and run["tabId"] != tab and run["result"]["verdict"] == "VERIFIED", run["log"]
    assert x.state()["recordings"][rec["id"]]["lastRun"]["verdict"] == "VERIFIED"
    notes = x.sw.evaluate("NOTICES")
    assert any("VERIFIED" in n["title"] for n in notes)
    time.sleep(1.0)
    assert x.sw.evaluate("(t) => chrome.tabs.get(t).then(() => 'open', () => 'closed')", run["tabId"]) == "closed"
    assert x.sw.evaluate("(id) => chrome.alarms.get('awos-sched:' + id).then(a => !!a)", rec["id"])   # re-armed


def test_scheduled_run_pauses_for_missing_param_and_approval(x):
    page = x.ctx.new_page()
    rec, tab = _record_form(x, page)
    x.call("set_param", id=rec["id"], step=1, param={"name": "fullname", "label": "Whose profile", "example": "Grace"})
    x.call("set_schedule", id=rec["id"], schedule={"kind": "daily", "time": "06:00"})
    _fire(x, rec["id"])
    run = x.wait_run(statuses=("needs_user", "done", "failed"))
    assert run["status"] == "needs_user" and run["pending"]["needParams"][0]["name"] == "fullname", run["log"]
    assert "needs you" in x.sw.evaluate("NOTICES")[0]["title"]
    assert "enter a value" in _err(x, "continue", runId=run["id"], nonce=run["pending"]["nonce"])
    x.call("continue", runId=run["id"], nonce=run["pending"]["nonce"], params={"fullname": "Grace Hopper"})
    run = x.wait_run()
    assert run["result"]["verdict"] == "FAILED"            # the recorded check still names Ada: a param changes the page
    assert x.state()["recordings"][rec["id"]]["lastParams"] == {"fullname": "Grace Hopper"}

    page2 = x.ctx.new_page()
    rec2, _ = _record_checkout(x, page2)
    x.call("set_schedule", id=rec2["id"], schedule={"kind": "weekly", "time": "06:00", "day": 1})
    _fire(x, rec2["id"])
    run = x.wait_run(statuses=("awaiting_approval", "done", "failed"))
    assert run["status"] == "awaiting_approval" and "Pay now" in run["pending"]["message"], run["log"]
    assert "needs you" in x.sw.evaluate("NOTICES")[0]["title"]
    time.sleep(1.5)
    assert x.state()["run"]["status"] == "awaiting_approval"                      # nothing auto-approves
    sched_page = next(p for p in x.ctx.pages if p.url.startswith(x.server.base + "/cart.html") and p is not page2)
    assert sched_page.inner_text("#status") == "Not paid"

    # a schedule firing while a run is paused is skipped, recorded and notified; the paused run is untouched
    _fire(x, rec["id"])
    st = x.state()
    assert st["runs"][0]["result"]["code"] == "BUSY" and st["runs"][0]["scheduled"]
    assert st["run"]["id"] == run["id"] and st["run"]["status"] == "awaiting_approval"
    assert "skipped" in x.sw.evaluate("NOTICES")[0]["title"]
    x.call("reject", runId=run["id"], nonce=run["pending"]["nonce"])


def test_scheduled_runs_gate_submit_like_clicks(x):
    """Unattended: "Save profile" passes, but the stricter vocabulary is what the act gate uses."""
    page = x.ctx.new_page()
    rec, _ = _record_form(x, page)
    all_ = x.sw.evaluate("chrome.storage.local.get('recordings').then(o => o.recordings)")
    all_[rec["id"]]["steps"][4]["loc"]["name"] = "Submit profile"     # as if recorded on a "Submit" button
    x.sw.evaluate("(r) => chrome.storage.local.set({recordings: r})", all_)
    x.call("set_schedule", id=rec["id"], schedule={"kind": "daily", "time": "06:00"})
    _fire(x, rec["id"])
    run = x.wait_run(statuses=("awaiting_approval", "done", "failed"))
    assert run["status"] == "awaiting_approval" and run["pending"]["danger"] == "submit", run["log"]
    x.call("reject", runId=run["id"], nonce=run["pending"]["nonce"])


# ---- downloads -----------------------------------------------------------------------------------------------

def _record_download(x, page):
    page.goto(x.server.base + "/download.html")
    tab = x.tab_id(page)
    x.call("start_recording", tabId=tab, name="statement")
    with page.expect_download():
        page.click("#dl")
    time.sleep(0.5)
    return x.call("stop_recording"), tab


def test_download_check_pass_and_fail(x):
    page = x.ctx.new_page()
    rec, tab = _record_download(x, page)
    x.call("save_recording", id=rec["id"], expect={"urlPattern": "", "texts": []}, expectDownload={"nameRegex": r"statement-\d{4}-\d{2}\.csv$"})
    page.goto(x.server.base + "/help.html")
    x.call("run", recId=rec["id"], tabId=tab)
    run = x.wait_run()
    dl = [c for c in run["result"]["checks"] if c["kind"] == "download"]
    assert run["result"]["verdict"] == "VERIFIED" and dl[0]["ok"] and dl[0]["actual"] == "statement-2026-10.csv", run["result"]

    # the verifier reads the page, not the downloads list: with the download check passed its NOT_DONE is
    # logged but does not downgrade (the cloud smoke's GitHub release chore ended CHECK this way)
    x.server.verify_answer = "NOT_DONE: done was claimed without it"
    page.goto(x.server.base + "/help.html")
    x.call("run", recId=rec["id"], tabId=tab)
    run = x.wait_run()
    assert run["result"]["verdict"] == "VERIFIED" and run["verifier"]["label"] == "NOT_DONE", run["result"]
    assert any("verifier disagreement not applied" in e["text"] for e in run["log"]), run["log"]
    x.server.verify_answer = "VERIFIED: fake"

    x.call("save_recording", id=rec["id"], expectDownload={"nameRegex": r"invoice.*\.pdf$"})
    page.goto(x.server.base + "/help.html")
    x.call("run", recId=rec["id"], tabId=tab)
    run = x.wait_run()
    dl = [c for c in run["result"]["checks"] if c["kind"] == "download"]
    assert run["result"]["verdict"] == "FAILED" and not dl[0]["ok"] and "statement" in dl[0]["actual"], run["result"]
    assert "valid regex" in _err(x, "save_recording", id=rec["id"], expectDownload={"nameRegex": "("})


# ---- brain -------------------------------------------------------------------------------------------------------

FAKE_FETCH = """() => {
  self.__realFetch = self.__realFetch || self.fetch;
  self.__claudeCalls = [];
  self.fetch = async function (url, init) {
    url = String(url);
    if (url.indexOf('https://api.anthropic.com/') === 0) {
      var body = JSON.parse(init.body);
      self.__claudeCalls.push({url: url, headers: init.headers, body: body});
      var text;
      if (body.system.indexOf('You check whether') === 0) text = 'VERIFIED: fake claude';
      else {
        var lines = body.messages[0].content.split('Candidates on the page now:\\n')[1].split('\\n');
        var l = lines.find(function (s) { return /named 'Update'/.test(s); });
        text = l ? 'PICK ' + l.split('.')[0] + ': fake' : 'NOT_FOUND: fake';
      }
      return new Response(JSON.stringify({content: [{type: 'text', text: text}], stop_reason: 'end_turn'}),
                          {status: 200, headers: {'content-type': 'application/json'}});
    }
    if (url.indexOf('https://') === 0) throw new Error('unexpected network call ' + url);
    return self.__realFetch(url, init);
  };
}"""


def _claude_calls(x):
    return x.sw.evaluate("self.__claudeCalls")


def test_claude_brain_with_consent_and_key(x):
    x.sw.evaluate(FAKE_FETCH)
    page = x.ctx.new_page()
    rec, tab = _record_form(x, page)
    x.server.overrides["/form.html"] = "form_button_renamed.html"
    x.set_settings(brain="claude", cloudConsent=True)
    x.call("save_claude_key", key=KEY)
    page.goto(x.server.base + "/help.html")
    x.call("run", recId=rec["id"], tabId=tab)
    run = x.wait_run()
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    assert run["verifier"]["label"] == "VERIFIED" and run["verifier"]["brain"] == "claude"
    calls = _claude_calls(x)
    assert len(calls) == 2 and x.server.model_calls == [] and x.server.verify_calls == []   # nothing went to the local model
    c = calls[0]
    assert c["url"] == "https://api.anthropic.com/v1/messages"
    assert c["headers"]["x-api-key"] == KEY and c["headers"]["anthropic-version"] == "2026-10-21"
    assert c["headers"]["anthropic-dangerous-direct-browser-access"] == "true"
    b = c["body"]
    assert b["model"] == "claude-haiku-5-5" and b["thinking"] == {"type": "disabled"}
    assert "grammar" not in b and "temperature" not in b
    assert b["system"] == x.sw.evaluate("AWOSModel.SYSTEM") and b["messages"][0]["role"] == "user"
    assert b["messages"][0]["content"].startswith("The step used: button named 'Save profile'")
    assert calls[1]["body"]["system"] == x.sw.evaluate("AWOSModel.VERIFY_SYSTEM")
    # the key stays in storage: not in get_state, not in the exported run log
    st = x.state()
    assert KEY not in json.dumps(st) and st["claudeKeySet"] is True and st["settings"]["brain"] == "claude"
    log = x.call("export_log")
    assert KEY not in json.dumps(log) and log["model"]["brain"] == "claude" and log["model"]["name"] == "claude-haiku-5-5"


def test_claude_brain_without_consent_never_calls_the_cloud(x):
    x.sw.evaluate(FAKE_FETCH)
    page = x.ctx.new_page()
    rec, tab = _record_form(x, page)
    x.server.overrides["/form.html"] = "form_button_renamed.html"
    x.call("save_claude_key", key=KEY)
    s = x.set_settings(brain="claude", cloudConsent=False)
    assert s["cloudConsent"] is False
    page.goto(x.server.base + "/help.html")
    x.call("run", recId=rec["id"], tabId=tab)
    run = x.wait_run()
    assert run["result"]["code"] == "NOT_FOUND" and "consent" in run["result"]["reason"], run["result"]
    assert _claude_calls(x) == [] and x.server.model_calls == []
    b = _raw(x, "brain_status")
    assert b["brain"] == "claude" and not b["ok"] and "consent" in b["error"]
    # consent is meaningless without the Claude brain: switching back to local clears it
    assert x.set_settings(brain="local", cloudConsent=True)["cloudConsent"] is False
    assert "Anthropic API key" in _err(x, "save_claude_key", key="not-a-key")


def test_local_brain_down_says_so_and_offers_claude(x):
    x.sw.evaluate(FAKE_FETCH)
    x.set_settings(endpoint="http://127.0.0.1:9/v1")
    b = _raw(x, "brain_status")
    assert b["brain"] == "local" and not b["ok"] and "Claude (your own API key)" in b["error"]
    page = x.ctx.new_page()
    rec, tab = _record_form(x, page)
    x.server.overrides["/form.html"] = "form_deleted.html"
    page.goto(x.server.base + "/help.html")
    x.call("run", recId=rec["id"], tabId=tab)
    run = x.wait_run()
    assert "unreachable" in run["result"]["reason"] and "Claude (your own API key)" in run["result"]["reason"]
    assert _claude_calls(x) == []                                                  # no silent fallback to the cloud
    popup = x.popup(tab)
    popup.wait_for_selector("#brainwarn:not([hidden])")
    assert "Local model is not running" in popup.inner_text("#brainwarn")
