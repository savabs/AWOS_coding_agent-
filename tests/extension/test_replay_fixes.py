"""Regressions for what the real-site cloud smoke (run 40654004310) found, each on a local fixture page:

  lazy.html          GitHub release assets: the target loads only when scrolled into view -> replay scrolls
                     in steps and re-scans before asking the model
  spa_nav.html       OpenStreetMap "Copyright": a pushState route that changes the URL ~1.2 s after the click and
                     renders later -> replay waits for the URL change / DOM quiet, final checks retry once
  shadow.html        MDN search: every control is in an OPEN shadow root -> recorded via composedPath, found
                     through shadow roots; a recording with only gotos is flagged and never VERIFIED
  dupes.html         python docs: two identical "Quick search" boxes -> collapsed, no model call
  enter_search.html  Enter in a search field is a `press Enter` step, not a typed URL
  (verifier)         the verifier sees the current address and the PASS / FAIL of every recorded check
Run:  PYTHONPATH=. ~/.awos/rl-venv/bin/python -m pytest tests/extension -q
"""
import time

import pytest

pytestmark = pytest.mark.timeout(120)


def _record(x, path, actions, name, texts=(), url_pattern=None, settle=0.8):
    page = x.ctx.new_page()
    page.goto(x.server.base + path)
    tab = x.tab_id(page)
    x.call("start_recording", tabId=tab, name=name)
    actions(page)
    time.sleep(settle)
    rec = x.call("stop_recording")
    rec = x.call("save_recording", id=rec["id"], goal=name,
                 expect={"urlPattern": url_pattern if url_pattern is not None else rec["expect"]["urlPattern"],
                         "texts": list(texts)})
    return page, tab, rec


def _replay(x, page, rec, tab):
    page.goto(x.server.base + "/help.html")
    x.call("run", recId=rec["id"], tabId=tab)
    return x.wait_run(timeout=60)


# ---- 1. lazy-loaded targets --------------------------------------------------------------------------------

def _lazy_actions(page):
    page.locator("#sentinel").scroll_into_view_if_needed()
    page.wait_for_selector("#asset")
    time.sleep(0.3)
    page.click("#asset")
    page.wait_for_url("**/lazy_done.html*")


def test_lazy_loaded_target_found_by_scrolling(x):
    page, tab, rec = _record(x, "/lazy.html", _lazy_actions, "lazy", texts=["File: sha256sum.txt"])
    click = [s for s in rec["steps"] if s["action"] == "click"]
    assert len(click) == 1 and click[0]["loc"]["name"] == "sha256sum.txt" and click[0]["scrollY"] > 0, rec["steps"]
    run = _replay(x, page, rec, tab)
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    assert x.server.model_calls == []                              # found by scrolling, not by asking the model
    assert any("scrolled" in l["text"] and "found" in l["text"] for l in run["log"]), run["log"]
    assert run["resolved"][-1]["how"].startswith("score")


def test_scroll_search_is_bounded_when_target_is_gone(x):
    """the asset was removed: scrolling gives up (<= 6 s) and the model still gets the final say."""
    page, tab, rec = _record(x, "/lazy.html", _lazy_actions, "lazy-gone", texts=["File: sha256sum.txt"])
    x.server.overrides["/lazy.html"] = "help.html"
    t0 = time.time()
    run = _replay(x, page, rec, tab)
    assert run["result"]["code"] == "NOT_FOUND", run["log"]
    assert time.time() - t0 < 30


# ---- 2. SPA navigation -------------------------------------------------------------------------------------

def _spa_actions(page):
    page.click("#copyright")
    page.wait_for_url("**/spa_copyright")
    page.wait_for_selector("text=Open Database License")


def test_spa_pushstate_navigation_waits_before_checks(x):
    page, tab, rec = _record(x, "/spa_nav.html", _spa_actions, "spa route", texts=["Open Database License"])
    assert rec["expect"]["urlPattern"] == x.server.base + "/spa_copyright"
    click = next(s for s in rec["steps"] if s["action"] == "click")
    assert click.get("navigates") is True, rec["steps"]
    run = _replay(x, page, rec, tab)
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    assert any("page changed to" in l["text"] for l in run["log"]), run["log"]
    assert page.url == x.server.base + "/spa_copyright"


def test_final_checks_retry_once_before_failing(x):
    """the click was not seen as navigating (recorded steps edited), so only the final-check retry catches the late
    route: it waits, looks again, and passes instead of failing ~600 ms after the click."""
    page, tab, rec = _record(x, "/spa_nav.html", _spa_actions, "spa late", texts=["Open Database License"])
    all_ = x.sw.evaluate("chrome.storage.local.get('recordings').then(o => o.recordings)")
    for s in all_[rec["id"]]["steps"]:
        s.pop("navigates", None)
    x.sw.evaluate("(r) => chrome.storage.local.set({recordings: r})", all_)
    run = _replay(x, page, rec, tab)
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    assert any("retrying once" in l["text"] for l in run["log"]), run["log"]


# ---- 3. shadow DOM + recordings with no actions --------------------------------------------------------------

def _shadow_actions(page):
    page.click("doc-search >> #open")
    time.sleep(0.3)
    page.fill("doc-search >> #q", "map")
    time.sleep(0.3)
    page.click("doc-search >> [role=option]:text-is('Array.prototype.map()')")
    page.wait_for_url("**/shadow_done.html*")


def test_shadow_dom_controls_recorded_and_replayed(x):
    page, tab, rec = _record(x, "/shadow.html", _shadow_actions, "shadow", texts=["Array.prototype.map()", "callbackFn"])
    acts = [s["action"] for s in rec["steps"]]
    assert acts == ["goto", "click", "fill", "click"], rec["steps"]
    assert not rec.get("warning")
    assert rec["steps"][2]["loc"]["name"] == "Search the docs" and rec["steps"][2]["value"] == "map"
    assert rec["steps"][3]["loc"]["role"] == "option" and rec["steps"][3]["navigates"] is True
    run = _replay(x, page, rec, tab)
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    assert len(run["resolved"]) == 3 and all(r["how"].startswith("score") for r in run["resolved"]), run["resolved"]
    assert x.server.model_calls == []


def test_recording_with_only_page_addresses_is_flagged_not_verified(x):
    def actions(page):
        page.goto(x.server.base + "/done.html?fullname=A&email=b&plan=pro")
    page, tab, rec = _record(x, "/help.html", actions, "urls only", texts=["Profile saved for A"])
    assert [s["action"] for s in rec["steps"]] == ["goto", "goto"]
    assert "Nothing was recorded on this page" in rec["warning"]
    run = _replay(x, page, rec, tab)
    assert run["result"]["verdict"] == "UNVERIFIED" and run["result"]["code"] == "NO_ACTIONS", run["result"]
    assert "nothing was recorded on this page" in run["result"]["reason"]
    assert all(c["ok"] for c in run["result"]["checks"])           # the page checks passed, the verdict says why it is not a pass
    popup = x.popup(tab)
    popup.wait_for_selector(f"li[data-rec='{rec['id']}']")
    assert "no actions recorded" in popup.inner_text(f"li[data-rec='{rec['id']}']")


# ---- 4. duplicate identical controls -----------------------------------------------------------------------

def test_identical_duplicate_controls_collapse_without_model(x):
    def actions(page):
        page.fill("form >> nth=0 >> input[name=q]", "json")
        page.click("form >> nth=0 >> input[type=submit]")
        page.wait_for_url("**/search_result.html*")
    page, tab, rec = _record(x, "/dupes.html", actions, "dupes", texts=["Results for json"])
    run = _replay(x, page, rec, tab)
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    assert x.server.model_calls == [], x.server.model_calls
    fill = next(r for r in run["resolved"] if r["step"] == 1)
    assert fill["how"].startswith("score") and "identical copies" in fill["how"], run["resolved"]


def test_different_controls_with_same_name_still_ambiguous(page_libs):
    """two "Delete" buttons in different rows are NOT copies (their block text differs): no collapse."""
    p = page_libs("/help.html")
    r = p.evaluate("""() => {
      document.body.innerHTML = '<ul><li>Invoice 7 <button id=a>Delete</button></li><li>Invoice 9 <button id=b>Delete</button></li></ul>'
        + '<div><span>Docs</span> <input id=c aria-label="Quick search"></div><div><span>Docs</span> <input id=d aria-label="Quick search"></div>';
      const L = AWOSLocator;
      const del = L.find(L.describe(document.getElementById('a')), 'click');
      const qs = L.find(L.describe(document.getElementById('c')), 'fill');
      return {del: del.status, delList: del.shortlist.length, qs: qs.status, qsDupes: qs.dupes,
              qsPick: L.element(qs.index).id};
    }""")
    assert r["del"] == "found" or r["delList"] == 2        # the recorded row wins on its own text, never by collapsing
    assert r["qs"] == "found" and r["qsDupes"] == 1 and r["qsPick"] == "c", r


# ---- 5. Enter-to-submit ------------------------------------------------------------------------------------

def test_enter_in_search_field_is_a_press_step(x):
    def actions(page):
        page.fill("#q", "turing")
        page.press("#q", "Enter")
        page.wait_for_url("**/search_result.html*")
    page, tab, rec = _record(x, "/enter_search.html", actions, "enter", texts=["Results for turing"])
    acts = [s["action"] for s in rec["steps"]]
    assert acts == ["goto", "fill", "press"], rec["steps"]           # no click on Search, no goto ?q=turing
    assert rec["steps"][2]["key"] == "Enter" and rec["steps"][2]["navigates"] is True
    run = _replay(x, page, rec, tab)
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    assert "Results for turing" in page.inner_text("#msg")
    assert any(a["action"].startswith("press(") for a in run.get("actions", []))
    recipe = x.call("export_recipe", id=rec["id"])["recipe"]
    assert recipe["steps"][2]["action"] == "press" and recipe["steps"][2]["key"] == "Enter"


def test_enter_in_js_search_box_without_form(x):
    def actions(page):
        page.fill("#js", "abc")
        page.press("#js", "Enter")
        page.wait_for_url("**/search_result.html*")
    page, tab, rec = _record(x, "/enter_search.html", actions, "enter js", texts=["Results for js:abc"])
    assert [s["action"] for s in rec["steps"]] == ["goto", "fill", "press"], rec["steps"]
    run = _replay(x, page, rec, tab)
    assert run["result"]["verdict"] == "VERIFIED", run["log"]


# ---- 6. verifier evidence ----------------------------------------------------------------------------------

def test_verifier_gets_current_url_and_check_results(x):
    page, tab, rec = _record(x, "/spa_nav.html", _spa_actions, "osm", texts=["Open Database License"])
    x.call("save_recording", id=rec["id"], goal="osm", expect={"urlPattern": x.server.base + "/elsewhere", "texts": ["Open Database License"]})
    run = _replay(x, page, rec, tab)
    assert run["result"]["verdict"] == "FAILED", run["log"]
    user = x.server.verify_calls[-1]["messages"][1]["content"]
    assert "URL path: /spa_copyright  (HTTP 200)" in user, user[:600]
    assert f"Current address: {x.server.base}/spa_copyright" in user
    assert f"address is {x.server.base}/elsewhere: FAIL" in user and 'page shows "Open Database License": PASS' in user


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
        for lib in ("lib/safety.js", "lib/locator.js", "lib/model.js"):
            p.add_script_tag(path=str(ext / lib))
        return p
    yield load
    b.close()
