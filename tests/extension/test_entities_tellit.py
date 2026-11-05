"""AWOS Replay 0.10: Tell it and goal-guided steps see the page as typed entities and plan on a learned site map.

On the claude-like fixture (tests/extension/pages/app/home.html: profile button "S Saurabh · Max" -> menu "Settings" ->
Settings window, tabs Account / Billing / ... -> 4 invoice rows with identical "Download PDF"; option `chats` adds a
"Recents" chat history to the sidebar). The brain is FAKE (test_tell_it.Brain: scripted micro-agent replies that find
controls by name in the prompt); plan calls are answered by PlanBrain. No real model, no network.

  - the v2 prompt: the profile button pinned as account navigation, the open menu's "Settings" first, chat history
    collapsed to one line, invoice rows grouped with their dates / amounts; the grammar offers only the shown numbers
  - a recording builds the site map: route profile -> Settings -> Billing -> Download PDF; "Known paths" reaches the
    Tell-it prompt, the plan call (2-5 subgoals) runs once and its current subgoal advances with each click's effect
  - Tell it learns effects into chrome.storage.local 'siteMap' ('seen' per action, 'ok' once the user confirms)
  - a click that changes nothing is set aside (not shown again on that page) and two of them trigger one replan
  - settings.pageView = false keeps the v0.5 prompt (the micro-agent contract)
Run:  PYTHONPATH=. ~/.awos/rl-venv/bin/python -m pytest tests/extension -q -k entities
"""
import re
import time

import pytest

from test_tell_it import Brain, _tell, _wait_tell
from test_realportal import HOME, _full_route, _record

pytestmark = pytest.mark.timeout(240)
PLAN = "- Open the profile menu\n- Open Settings\n- Open the Billing tab\n- Download the October invoice\n"


class PlanBrain(Brain):
    """Brain + the plan call (system starts 'You plan a web task'): answers `plan` (NOT_FOUND = no plan)."""

    def __init__(self, script, plan=None):
        super().__init__(script)
        self.plan = plan
        self.plan_requests = []

    def __call__(self, body):
        if body["messages"][0]["content"].startswith("You plan a web task"):
            self.plan_requests.append(body)
            return self.plan or "NOT_FOUND: fake"
        return super().__call__(body)


def _page(x, state=None):
    x.server.state.clear()
    x.server.state.update(state or {})
    page = x.ctx.new_page()
    page.goto(x.server.base + HOME)
    return page


def _observe(x, page):
    return x.sw.evaluate("(tab) => ensureContent(tab).then(() => sendRetry(tab, {type: 'agent_observe'})).then(r => AWOSAgent.maskObs(r))",
                         x.tab_id(page))


def _v2(x, obs, goal, **kw):
    return x.sw.evaluate("([o, g, kw]) => AWOSAgent.buildPromptV2(Object.assign({goal: g, step: 1, maxSteps: 25, obs: o, now: nowMs()}, kw))",
                         [obs, goal, kw])


def _num(text, role, name):
    m = re.search(r'^\[(\d+)\] ' + re.escape(role) + ' "' + re.escape(name) + '"', text, re.M)
    return int(m[1]) if m else None


def _block(text, header):
    """the control lines under one entity header (up to the next header)."""
    lines = text.split("\n")
    i = next(k for k, l in enumerate(lines) if l.startswith(header))
    out = []
    for l in lines[i + 1:]:
        if not l.startswith("["):
            break
        out.append(l)
    return out


def _setup(x, script, plan=None, **settings):
    brain = PlanBrain(script, plan)
    x.server.model_answer = brain
    x.set_settings(**settings)
    return brain


def _site_map(x):
    return x.sw.evaluate("getLocal('siteMap', {hosts: {}})")


def _edges(x, host="127.0.0.1"):
    return list(((_site_map(x).get("hosts") or {}).get(host) or {}).get("edges", {}).values())


# ---- 1. the entity view on the claude-like page -------------------------------------------------------------------

def test_v2_prompt_pins_account_nav_hides_chat_history_and_groups_invoice_rows(x):
    page = _page(x, {"chats": 40})
    obs = _observe(x, page)
    assert len(obs["controls"]) > 40 and obs.get("structure"), len(obs["controls"])
    pv = _v2(x, obs, "download my October invoice")
    text = pv["text"]
    obs_text = text.split("<observation", 1)[1]
    # the profile button is account navigation, pinned; the chat history is one collapsed line, no chat is listed
    assert _num(obs_text, "button", "S Saurabh · Max") in pv["shown"], text
    assert '[%d] button "S Saurabh · Max"' % _num(obs_text, "button", "S Saurabh · Max") in "\n".join(_block(obs_text, "Account and settings:")), text
    assert re.search(r'^List .*: 40 links hidden$', obs_text, re.M), text
    assert "Chat about topic" not in obs_text.split("Page text:", 1)[1].split("Controls (", 1)[1], text
    assert re.search(r"^Controls \(\d+ total, %d shown, grouped" % len(pv["shown"]), obs_text, re.M)
    # the grammar offers only the shown numbers: a hidden chat link cannot be clicked
    grammar = x.sw.evaluate("(s) => AWOSAgent.buildGrammarFor(s, true, {scroll: true})", pv["shown"])
    nums = [int(n) for n in re.findall(r'"(\d+)"', re.search(r"^num ::= (.*)$", grammar, re.M)[1])]
    assert nums == sorted(pv["shown"]) and len(nums) <= 40
    chat = next(i + 1 for i, c in enumerate(obs["controls"]) if c["name"] == "Chat about topic 7")
    assert chat not in nums
    err = x.sw.evaluate("([r, g]) => AWOSAgent.grammarError(r, g)", ["click(%d)" % chat, grammar])
    assert "is not on the page" in err, err
    assert x.sw.evaluate("([r, g]) => AWOSAgent.grammarError(r, g)", ["click(%d)" % nums[0], grammar]) == ""
    v = x.sw.evaluate("([o, s, h]) => AWOSAgent.validateV2({verb: 'click', n: %d}, o, s, h, false)" % chat, [obs, pv["shown"], pv["hidden"]])
    assert "not shown" in v["error"], v
    assert "scroll(down) lists every control" in text
    # scroll(down) = expand: every control, still grouped
    full = _v2(x, obs, "download my October invoice", expand=True)
    assert chat in full["shown"] and full["hidden"] == 0 and "Chat about topic 7" in full["text"]

    # the profile menu open: its "Settings" is the open account menu, listed first
    page.click("#prof")
    obs = _observe(x, page)
    text = _v2(x, obs, "download my October invoice")["text"].split("<observation", 1)[1]
    menu = _block(text, 'Account menu (open) "Profile":')
    assert any('menuitem "Settings"' in l for l in menu), text
    assert text.index('Account menu (open)') < text.index('Account and settings:'), text

    # Settings open on Billing: the invoice rows are one list with their dates, amounts and status
    page.close()
    page = _page(x, {"chats": 40, "open": "billing"})
    obs = _observe(x, page)
    pv = _v2(x, obs, "download my October invoice")
    text = pv["text"].split("<observation", 1)[1]
    assert 'Window "Settings" — tabs: "Account" · "Billing" (selected) · "Usage" · "Appearance"' in text, text
    hdr = next(l for l in text.split("\n") if l.startswith("Invoice list"))
    assert "(4 rows: Oct 1, 2026 $200.00 Paid | Sep 1, 2026 $100.00 Paid" in hdr, hdr
    rows = _block(text, hdr)
    assert len(rows) == 4 and all('link "Download PDF"' in r for r in rows), rows
    assert rows[0].endswith('(row "Oct 1, 2026 $200.00 Paid")') and rows[1].endswith('(row "Sep 1, 2026 $100.00 Paid")'), rows
    assert "Chat about topic" not in text.split("Page text:", 1)[1], text        # the hidden chat history is not in the text either


def test_page_view_off_keeps_the_micro_agent_prompt(x):
    brain = _setup(x, ['done "nothing to do"'], pageView=False)
    page = _page(x, {"chats": 40})
    x.call("tell_start", tabId=x.tab_id(page), goal="Say done")
    t = _wait_tell(x, ("confirm", "ended", "needs_user"))
    assert t["traj"]["promptVersion"] == "v1"
    u = brain.requests[0]["messages"][1]["content"]
    assert re.search(r"^Controls \(\d+ total, showing 1-40\):", u, re.M) and "Account and settings:" not in u, u
    assert re.search(r"^num ::= \"1\" \| \"2\"", brain.requests[0]["grammar"], re.M)


# ---- 2. site map from a recording -> route, known paths in the prompt, plan-then-act ---------------------------------

def test_recording_builds_route_known_paths_and_plan_advances(x):
    page, tab, rec, _ = _record(x, _full_route)
    route = x.sw.evaluate("""(h) => siteMapLoad(h).then(m => AWOSSiteMap.route(m, h, '127.0.0.1/app/home.html', 'download my October invoice')
                                     .map(e => e.control.role + ' ' + e.control.name + ' ' + e.effect.kind))""", "127.0.0.1")
    assert route == ["button S Saurabh · Max opened_menu", "menuitem Settings opened_dialog", "tab Billing switched_tab",
                     "link Download PDF downloaded"], route
    # v0.10 learning: a recording is merged when it is saved, as 'seen' (a VERIFIED replay makes it 'ok')
    assert all(e["seen"] == 1 and e["ok"] == 0 for e in _edges(x)), _edges(x)

    brain = _setup(x, ['click "S Saurabh · Max"', 'click "Settings"', 'click "Billing"', 'click "Download PDF"',
                       'done "Downloaded the October invoice"'], plan=PLAN)
    x.server.state.clear()
    page.goto(x.server.base + HOME)
    x.call("tell_start", tabId=tab, goal="download my October invoice", explore=True)
    t = _wait_tell(x, ("confirm", "ended", "needs_user", "awaiting_approval"), timeout=120)
    assert t["status"] == "confirm", t["feed"]
    assert len(brain.plan_requests) == 1, len(brain.plan_requests)
    pu = brain.plan_requests[0]
    assert pu["grammar"].startswith("root ::= item item item? item? item?") and "Known paths on 127.0.0.1:" in pu["messages"][1]["content"]
    p = [r["messages"][1]["content"] for r in brain.requests]
    assert 'Known paths on 127.0.0.1:\n- button "S Saurabh · Max" -> opens menu -> menuitem "Settings" -> opens the "Settings" window' in p[0], p[0]
    # the plan's current subgoal moves on with what each click did
    assert "> now: Open the profile menu" in p[0] and "> now: Open Settings" in p[1] and "> now: Open the Billing tab" in p[2], p[1]
    assert "> now: Download the October invoice" in p[3] and "  done: Open the Billing tab" in p[3], p[3]
    assert "it opened the menu of \"S Saurabh · Max\"" in p[1] and 'it opened the "Settings" window' in p[2], p[2]
    assert 'it switched to the "Billing" tab' in p[3], p[3]
    assert t["traj"]["promptVersion"] == "v2" and t["traj"]["plans"][0]["steps"][0] == "Open the profile menu"
    assert "/files/doc/invoice-2026-10.pdf" in x.server.hits


# ---- 3. Tell it learns what clicks do -------------------------------------------------------------------------------

def test_tell_it_effects_update_the_stored_site_map(x):
    brain = _setup(x, ['click "S Saurabh · Max"', 'click "Settings"', 'click "Billing"', 'done "Billing is open"'])
    page = _page(x)
    tab = x.tab_id(page)
    x.call("tell_start", tabId=tab, goal="show my billing")
    t = _wait_tell(x, ("confirm", "ended", "needs_user"))
    assert t["status"] == "confirm", t["feed"]
    assert brain.plan_requests == []                 # one verb, no known path: no plan call
    es = {(e["control"]["role"], e["control"]["name"]): e for e in _edges(x)}
    prof, sett, bill = es[("button", "S Saurabh · Max")], es[("menuitem", "Settings")], es[("tab", "Billing")]
    assert prof["effect"]["kind"] == "opened_menu" and prof["from"] == "127.0.0.1/app/home.html"
    assert prof["to"] == "127.0.0.1/app/home.html|menu:S Saurabh · Max" == sett["from"], (prof, sett)
    assert sett["effect"] == {"kind": "opened_dialog", "label": "Settings"} and sett["to"] == "127.0.0.1/app/home.html|dialog:Settings|tab:Account"
    assert bill["effect"]["kind"] == "switched_tab" and bill["to"].endswith("|dialog:Settings|tab:Billing")
    assert all(e["seen"] == 1 and e["ok"] == 0 for e in (prof, sett, bill)), (prof, sett, bill)
    assert not any("value" in e["control"] for e in es.values())
    x.call("tell_confirm", nonce=t["pending"]["nonce"], yes=True, name="Billing")
    es = {(e["control"]["role"], e["control"]["name"]): e for e in _edges(x)}
    assert all(es[k]["ok"] == 1 for k in [("button", "S Saurabh · Max"), ("menuitem", "Settings"), ("tab", "Billing")]), es
    # the saved Tell-it recipe is not merged into the map a second time
    x.sw.evaluate("siteMapLoad('127.0.0.1')")
    assert es[("tab", "Billing")]["ok"] == {(e["control"]["role"], e["control"]["name"]): e for e in _edges(x)}[("tab", "Billing")]["ok"]


# ---- 4. a click that changes nothing is set aside; two of them -> one replan -----------------------------------------

def test_ineffective_click_is_marked_tried_and_triggers_a_replan(x):
    brain = _setup(x, ['click "Customize"', 'click "Chats and tasks"', 'click "S Saurabh · Max"', 'done "menu open"'], plan=PLAN)
    page = _page(x)
    x.call("tell_start", tabId=x.tab_id(page), goal="find my invoices")
    t = _wait_tell(x, ("confirm", "ended", "needs_user"))
    assert t["status"] == "confirm", t["feed"]
    p = [r["messages"][1]["content"] for r in brain.requests]
    assert "1. click(" in p[1] and "nothing visible changed, so it is set aside on this page" in p[1], p[1]
    assert 'Tried on this page with no visible effect (not shown again): link "Customize".' in p[1], p[1]
    assert _num(p[1].split("<observation", 1)[1], "link", "Customize") is None and _num(p[0].split("<observation", 1)[1], "link", "Customize")
    assert 'link "Customize", button "Chats and tasks"' in p[2], p[2]
    # after two clicks that changed nothing, one plan call (none before: one verb, no known path)
    assert len(brain.plan_requests) == 1, len(brain.plan_requests)
    assert "> now: Open the profile menu" in p[2] and "> now:" not in p[1], p[2]
    assert [e.get("effect") for e in t["traj"]["events"] if e.get("effect")][:3] == ["nothing", "nothing", "opened_menu"]
