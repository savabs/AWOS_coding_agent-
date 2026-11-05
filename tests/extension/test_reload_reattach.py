"""v0.9.3: a tab opened BEFORE the extension is reloaded / updated keeps a dead copy of the page script; recording there
used to capture nothing (0 actions: the start signal failed silently). Now the worker injects a fresh copy whenever a
loaded page does not answer (and into every open tab at install / reload), and the newest copy takes over.

Headless Chromium unloads a command-line extension on chrome.runtime.reload(), so the test makes the tab's copy dead
the way v0.9.3 sees it: a newer generation is announced in its world, so the old copy stops answering and recording."""
import time


def _kill_page_script(x, tab):
    x.sw.evaluate("(t) => chrome.scripting.executeScript({target: {tabId: t}, func: () => { window.__awosReplayGen += 1000; }})", tab)
    answered = x.sw.evaluate("(t) => chrome.tabs.sendMessage(t, {type: 'ping'}).then(r => !!(r && r.ok), () => false)", tab)
    assert not answered, "the page script still answers"


def test_recording_in_a_tab_whose_page_script_is_dead_reattaches_and_records(x):
    page = x.ctx.new_page()
    page.goto(x.server.base + "/form.html")
    tab = x.tab_id(page)
    _kill_page_script(x, tab)
    x.call("start_recording", tabId=tab, name="after reload")
    page.fill("#fullname", "Ada Lovelace")
    page.click("text=Save profile")
    page.wait_for_url("**/done.html*")
    time.sleep(0.5)
    rec = x.call("stop_recording")
    actions = [s for s in rec["steps"] if s["action"] != "goto"]
    assert any(s["action"] == "click" for s in actions), rec["steps"]       # not 0 actions
    fills = [s for s in actions if s["action"] == "fill"]
    assert len(fills) == 1 and fills[0]["value"] == "Ada Lovelace"         # one copy recorded it, not two
    assert not rec.get("warning"), rec.get("warning")
