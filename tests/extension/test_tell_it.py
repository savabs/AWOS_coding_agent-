"""AWOS Replay 0.5 "Tell it", end to end in headless Chromium with the unpacked extension.

The brain is FAKE: the local test server answers /v1/chat/completions with scripted micro-agent replies
(scaffold/agent/desktop/micro_agent.ScriptedModel reads the observation exactly as a model would), and the
Claude path is a fake fetch inside the service worker. No real API calls.

  - goal -> steps executed -> done -> verifier -> confirm -> saved as a recording -> the recipe replays VERIFIED
  - private-data lookup on the local brain (fill_enter) -> recipe with fill + Enter replays VERIFIED
  - approval gate: a submit button and an unsubscribe button pause; nothing is clicked until Approve; "No" is fed back
  - password / code page -> the agent waits for the user; the agent's own attempt to type a password -> waits too;
    the recipe keeps a {{secret:...}} placeholder, the value is nowhere (recording, prompts, trajectory)
  - invalid replies -> one repair call each -> after 3 the run pauses and asks; the user's correction reaches the next prompt
  - cloud: never called without consent + key; a site lock wins over the setting; local endpoint down / 2B-only -> how to
    start a local agent model (no cloud suggestion)
  - caps: steps and time
  - the exported trajectory is the micro-agent Trajectory format (scaffold/rl/contract.py) and passes teacher.grammar_error
Run:  PYTHONPATH=. ~/.awos/rl-venv/bin/python -m pytest tests/extension -q -k tell
"""
import dataclasses
import json
import re
import time

import pytest

from scaffold.agent.desktop import micro_agent as MA
from scaffold.rl.contract import Step, Trajectory
from scaffold.rl.web import teacher as T

pytestmark = pytest.mark.timeout(150)
KEY = "sk-ant-test-0123456789abcdef"
AGENT_SYSTEM = MA.SYSTEM + MA.SYSTEM_WEB


class Brain:
    """The fake local agent model: scripted micro-agent replies for agent calls, NOT_FOUND for repair."""

    def __init__(self, script, delay=0.0):
        self.sm = MA.ScriptedModel(script)
        self.requests = []
        self.delay = delay

    def __call__(self, body):
        msgs = body["messages"]
        if not msgs[0]["content"].startswith("You operate a web dashboard"):
            return "NOT_FOUND: fake"
        self.requests.append(body)
        if self.delay:
            time.sleep(self.delay)
        return self.sm.decide(msgs[0]["content"], msgs[1]["content"], body.get("grammar"))


def _setup(x, script, **kw):
    brain = Brain(script, kw.pop("delay", 0.0))
    x.server.model_answer = brain
    s = {"agentEndpoint": x.server.base + "/v1", "agentModel": "fake-agent"}
    s.update(kw)
    x.set_settings(**s)
    return brain


def _start(x, path, goal, caps=None):
    page = x.ctx.new_page()
    page.goto(x.server.base + path)
    tab = x.tab_id(page)
    x.call("tell_start", tabId=tab, goal=goal, **({"caps": caps} if caps else {}))
    return page, tab


def _tell(x):
    return x.call("tell_state")["tell"]


def _wait_tell(x, statuses, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        t = _tell(x)
        if t and t["status"] in statuses:
            return t
        time.sleep(0.2)
    raise AssertionError(f"tell did not reach {statuses}: {json.dumps(_tell(x), indent=1)[:3000]}")


def _err(x, typ, **kw):
    return x.sw.evaluate("([t, m]) => HANDLERS[t](m, {}).then(() => '', e => String(e.message || e))", [typ, kw])


def _check_trajectory(ep):
    """the exported episode loads as scaffold.rl.contract.Trajectory and every valid step is the student's format."""
    sf = {f.name for f in dataclasses.fields(Step)}
    tf = {f.name for f in dataclasses.fields(Trajectory)}
    tr = Trajectory(**{**{k: ep[k] for k in tf if k != "steps"}, "steps": [Step(**{k: s[k] for k in sf}) for s in ep["steps"]]})
    assert tr.steps and tr.model and tr.task_id
    for s in tr.steps:
        assert s.system == AGENT_SYSTEM
        assert s.user.startswith("Goal: ") and f"Step {s.i} of " in s.user and '<observation untrusted="true">' in s.user
        n = int(re.search(r"^Controls \((\d+) total", s.user, re.M)[1])
        assert s.grammar == MA.build_grammar(n, web=True)
        if s.valid:
            assert T.grammar_error(s.reply, s.grammar) == "", s.reply
            assert MA.parse_text_action(s.reply).show() == s.action
    return tr


# ---- the main flow: goal -> run -> confirm -> recipe -> replay VERIFIED ---------------------------------------------

def test_goal_to_recipe_that_replays_verified(x):
    brain = _setup(x, ['click "Settings"', 'click "Marketing e-mails"', 'select "Language" "Deutsch"',
                       'click "Save settings"', 'done "Marketing e-mails are off and the language is Deutsch"'])
    page, tab = _start(x, "/account.html", "Turn off marketing e-mails and switch the language to Deutsch in my settings")
    t = _wait_tell(x, ("confirm", "ended", "needs_user", "awaiting_approval"))
    assert t["status"] == "confirm", t["feed"]
    assert "Marketing e-mails: off. Language: Deutsch" in page.inner_text("#msg")
    assert [s["action"] for s in t["steps"]] == ["goto", "click", "click", "select", "click"]
    assert t["steps"][1]["navigates"] and t["steps"][4]["navigates"] and not t["steps"][2].get("navigates")
    assert t["steps"][3]["optionText"] == "Deutsch" and t["steps"][3]["value"] == "de"
    pr = t["result"]["proposed"]
    assert pr["urlPattern"] == x.server.base + "/settings_saved.html"
    assert pr["texts"] == ["Settings saved. Marketing e-mails: off. Language: Deutsch"]
    assert t["result"]["verifier"]["label"] == "VERIFIED" and len(x.server.verify_calls) == 1
    # the live view: each step's action is in the feed; the prompts carry history and the page purpose
    assert [f["text"] for f in t["feed"] if f["kind"] == "step"][:2] == ["click(1)", "click(1)"]
    second = brain.requests[1]["messages"][1]["content"]
    assert '1. click(1) -> ok; now on "Settings"' in second and 'form to fill: "Notification settings"' in second
    assert len(brain.requests) == 5 and all(len(r["messages"]) == 2 for r in brain.requests)

    rec = x.call("tell_confirm", nonce=t["pending"]["nonce"], yes=True, name="Marketing off", expect=pr)
    assert rec["createdBy"] == "tell-it" and rec["goal"].startswith("Turn off marketing")
    assert rec["expect"] == {"urlPattern": pr["urlPattern"], "texts": pr["texts"]}
    assert x.state()["recordings"][rec["id"]]["name"] == "Marketing off"
    assert _tell(x)["status"] == "ended" and _tell(x)["outcome"] == "verified"
    x.call("tell_clear")

    page.goto(x.server.base + "/help.html")
    x.call("run", recId=rec["id"], tabId=tab)
    run = x.wait_run()
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    assert "Marketing e-mails: off. Language: Deutsch" in page.inner_text("#msg")

    data = x.call("tell_export_training")
    assert data["kind"] == "awos-tellit-trajectories/1" and len(data["episodes"]) == 1
    ep = data["episodes"][0]
    tr = _check_trajectory(ep)
    assert tr.outcome == "verified" and ep["user_confirmed"] is True and ep["recording"] == rec["id"]
    assert [s.action for s in tr.steps][-1].startswith("done(") and all(s.valid for s in tr.steps)
    assert ep["goal"].startswith("Turn off marketing") and tr.model == "local:fake-agent"


def test_private_lookup_on_local_brain_replays(x):
    brain = _setup(x, ['fill_enter "Order number" "4417"', 'done "Order 4417 (Grace Hopper): Shipped"'])
    page, tab = _start(x, "/admin.html", "Look up order 4417 in the orders admin and tell me its status")
    t = _wait_tell(x, ("confirm", "ended", "needs_user", "awaiting_approval"))
    assert t["status"] == "confirm", t["feed"]
    assert t["result"]["answer"] == "Order 4417 (Grace Hopper): Shipped"
    acts = [s["action"] for s in t["steps"]]
    assert acts == ["goto", "fill", "press"] and t["steps"][2]["navigates"] and t["steps"][1]["value"] == "4417"
    assert t["brain"] == "local" and len(brain.requests) == 2
    rec = x.call("tell_confirm", nonce=t["pending"]["nonce"], yes=True, name="Order lookup", expect=t["result"]["proposed"])
    assert rec["expect"]["texts"] == ["1 order found for 4417"]
    x.call("tell_clear")
    # a typed value is offered as a param exactly like a recorded one
    rec = x.call("set_param", id=rec["id"], step=1, param={"name": "order", "label": "Order number", "example": "4417"})
    x.call("save_recording", id=rec["id"], expect={"urlPattern": rec["expect"]["urlPattern"], "texts": ["1 order found for {{order}}"]})
    page.goto(x.server.base + "/help.html")
    x.call("run", recId=rec["id"], tabId=tab, params={"order": "4417"})
    run = x.wait_run()
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    assert page.inner_text("#s") == "Shipped"


# ---- safety gates ----------------------------------------------------------------------------------------------------

def test_submit_waits_for_approval(x):
    brain = _setup(x, ['fill "First name" "Ada"', 'fill "Email" "ada@example.com"', 'click "Submit application"',
                       'done "Application received"'])
    page, _ = _start(x, "/apply.html", "Fill the Backend engineer application with my name and e-mail and send it")
    t = _wait_tell(x, ("awaiting_approval", "confirm", "ended"))
    assert t["status"] == "awaiting_approval", t["feed"]
    assert t["pending"]["target"] == "Submit application" and t["pending"]["danger"] == "submit"
    time.sleep(1.0)
    assert page.url.endswith("/apply.html") and page.input_value("#first") == "Ada"      # nothing submitted
    assert len(brain.requests) == 3
    assert "stale" in _err(x, "tell_approve", nonce="wrong")
    x.call("tell_approve", nonce=t["pending"]["nonce"])
    t = _wait_tell(x, ("confirm", "ended", "awaiting_approval"))
    assert t["status"] == "confirm" and page.url.split("?")[0].endswith("/applied.html"), t["feed"]
    click = t["steps"][-1]
    assert click["action"] == "click" and click["navigates"] and click["loc"]["name"] == "Submit application"
    assert any(e.get("approved") for e in t["traj"]["events"])


def test_unsubscribe_declined_is_fed_back_and_injection_flagged(x):
    brain = _setup(x, ['click "Unsubscribe"', 'done "Kept the newsletter"'])
    page, _ = _start(x, "/newsletter.html", "Unsubscribe me from this newsletter")
    t = _wait_tell(x, ("awaiting_approval", "confirm", "ended"))
    assert t["status"] == "awaiting_approval" and t["pending"]["danger"] == "unsubscribe", t["feed"]
    x.call("tell_decline", nonce=t["pending"]["nonce"])
    t = _wait_tell(x, ("confirm", "ended"))
    assert page.inner_text("#state") == "Subscribed"                                      # never clicked
    second = brain.requests[1]["messages"][1]["content"]
    assert "1. click(1) -> REJECTED: the owner did not approve this" in second
    assert "WARNING: this page contains text addressed to AI agents" in second
    assert t["traj"]["flags"] and t["traj"]["flags"][0]["type"] == "prompt_injection"
    x.call("tell_confirm", nonce=t["pending"]["nonce"], yes=False)
    t = _tell(x)
    assert t["status"] == "ended" and t["outcome"] == "completed_unverified" and not t.get("savedRecId")
    ep = x.call("tell_export_training")["episodes"][0]
    assert ep["user_confirmed"] is False and ep["outcome"] == "completed_unverified"
    _check_trajectory(ep)


def test_password_page_waits_for_the_user(x):
    brain = _setup(x, ['fill "Password" "guess"', 'fill "Username" "ada"', 'click "Next"', 'done "Next pressed"'])
    page, _ = _start(x, "/login.html", "Sign in as ada and press Next")
    t = _wait_tell(x, ("needs_user", "awaiting_approval", "confirm", "ended"))
    assert t["status"] == "needs_user" and t["pending"]["kind"] == "guard", t["feed"]
    assert "yourself" in t["pending"]["message"] and brain.requests == []                # asked before any model call
    page.fill("#pw", "hunter2-SECRET")                                                    # the user types it
    x.call("tell_continue", nonce=t["pending"]["nonce"])
    t = _wait_tell(x, ("needs_user", "confirm", "ended"))
    assert t["status"] == "needs_user" and t["pending"]["kind"] == "secret", t["feed"]   # the agent tried to type it
    assert page.input_value("#pw") == "hunter2-SECRET"
    x.call("tell_continue", nonce=t["pending"]["nonce"])
    t = _wait_tell(x, ("confirm", "ended", "needs_user"))
    assert t["status"] == "confirm" and page.inner_text("#out") == "Next pressed", t["feed"]
    assert "SKIPPED: the owner types passwords and codes themselves" in brain.requests[1]["messages"][1]["content"]
    secret = [s for s in t["steps"] if s.get("sensitive")]
    assert len(secret) == 1 and secret[0]["value"] == "{{secret:password}}" and secret[0]["loc"]["id"] == "pw"
    rec = x.call("tell_confirm", nonce=t["pending"]["nonce"], yes=True, name="sign in")
    blob = json.dumps([rec, brain.requests, x.call("tell_export_training")])
    assert "hunter2" not in blob


# ---- invalid replies, repair, the user's correction -----------------------------------------------------------------

def test_invalid_replies_repair_then_ask_and_correction_reaches_prompt(x):
    brain = _setup(x, ["raw I would click the settings link"] * 6 + ['click "Settings"', 'done "On the settings page"'])
    page, _ = _start(x, "/account.html", "Open my settings")
    t = _wait_tell(x, ("needs_user", "confirm", "ended"))
    assert t["status"] == "needs_user" and t["pending"]["kind"] == "invalid", t["feed"]
    assert len(brain.requests) == 6                                                       # 3 steps x (call + one repair)
    rep = brain.requests[1]["messages"]
    assert len(rep) == 4 and rep[2] == {"role": "assistant", "content": "I would click the settings link"}
    assert rep[3]["content"] == T.REPAIR.format(err="no line is an action")
    assert "1. (unparseable reply) -> rejected: not an action: I would click the settings link" in brain.requests[2]["messages"][1]["content"]
    x.call("tell_continue", nonce=t["pending"]["nonce"], note="use the Settings link in the list")
    t = _wait_tell(x, ("confirm", "ended", "needs_user"))
    assert t["status"] == "confirm", t["feed"]
    nxt = brain.requests[6]["messages"][1]["content"]
    assert nxt.split("\n")[2] == "Note from the owner: use the Settings link in the list"
    assert "Note from the owner" not in brain.requests[7]["messages"][1]["content"]         # used once
    x.call("tell_stop")
    ep = x.call("tell_export_training")["episodes"][0]
    assert [s["valid"] for s in ep["steps"]] == [False, False, False, True, True]
    assert ep["steps"][3]["note"] == "use the Settings link in the list"
    _check_trajectory(ep)


def test_no_start_page_needed(x):
    # a new tab (no web page) and no start page typed: it starts on the search page and tells the agent so
    brain = _setup(x, ['fill_enter "Order number" "4417"', 'done "Order 4417 (Grace Hopper): Shipped"'],
                   searchPage=x.server.base + "/admin.html")
    page = x.ctx.new_page()
    page.goto("about:blank")
    x.call("tell_start", tabId=x.tab_id(page), goal="Look up order 4417 in the orders admin and tell me its status")
    t = _wait_tell(x, ("confirm", "ended", "needs_user", "awaiting_approval"))
    assert t["status"] == "confirm" and t["startUrl"] == x.server.base + "/admin.html", t["feed"]
    assert "No site was open: you are on a search page" in brain.requests[0]["messages"][1]["content"]
    x.call("tell_stop")
    # with {goal} in the start page it opens the search RESULTS for the goal (no fragile search-box typing)
    page.close()                                                  # two about:blank tabs would confuse tab_id()
    brain = _setup(x, ['done "Results are open"'], searchPage=x.server.base + "/search_results.html?q={goal}")
    page = x.ctx.new_page()
    page.goto("about:blank")
    x.call("tell_start", tabId=x.tab_id(page), goal="price of Dior Homme Intense")
    t = _wait_tell(x, ("confirm", "ended", "needs_user", "awaiting_approval"))
    assert t["startUrl"] == x.server.base + "/search_results.html?q=price%20of%20Dior%20Homme%20Intense", t["startUrl"]
    assert "Results for price of Dior Homme Intense" in page.inner_text("#r")
    assert "web search results for the goal" in brain.requests[0]["messages"][1]["content"]
    x.call("tell_stop")
    # the bare DuckDuckGo home page saved by 0.5.0 Options is upgraded to the results template
    x.set_settings(searchPage="https://duckduckgo.com/")
    assert x.sw.evaluate("() => tellSettings().then(s => s.searchPage)") == "https://duckduckgo.com/?q={goal}"
    # a site named in the goal is opened directly; e-mail addresses and IPs are not sites
    f = "(g) => siteInGoal(g)"
    assert x.sw.evaluate(f, "find when the Eiffel Tower was finished on wikipedia.org please") == "https://wikipedia.org/"
    assert x.sw.evaluate(f, "open https://news.ycombinator.com/newest and read the top title") == "https://news.ycombinator.com/newest"
    assert x.sw.evaluate(f, "e-mail ada@example.com the report") == ""
    assert x.sw.evaluate(f, "check 10.0.0.1 status") == "" and x.sw.evaluate(f, "compare two shops") == ""


def test_enter_ignored_by_page_still_searches(x):
    # DuckDuckGo-like box: page code cancels the synthetic Enter and does nothing (a real run repeated the same
    # fill_enter 22 times); the form is then submitted the way the browser would
    _setup(x, ['fill_enter "Search the web" "dior homme intense price"', 'done "Searched"'])
    page, _ = _start(x, "/search_like.html", "Search the web for the price of Dior Homme Intense")
    t = _wait_tell(x, ("confirm", "ended", "needs_user", "awaiting_approval"))
    assert t["status"] == "confirm", t["feed"]
    assert page.url.split("?")[0].endswith("/search_results.html") and "dior homme intense price" in page.inner_text("#r")
    x.call("tell_stop")


def test_same_fill_enter_three_times_pauses(x):
    # the loop guard covers every action, not only clicks: the third identical fill_enter is refused and it asks
    brain = _setup(x, ['fill_enter "Search the web" "same query"'] * 6)
    _start(x, "/dead_search.html", "Search the web for the same query")
    t = _wait_tell(x, ("needs_user", "confirm", "ended", "awaiting_approval"), timeout=90)
    assert t["status"] == "needs_user" and t["pending"]["kind"] == "loop", t["feed"]
    assert 'fill_enter(1, "same query")' in t["pending"]["message"] and len(brain.requests) == 3
    x.call("tell_stop")


def test_popup_warms_the_local_agent_model_only(x):
    # opening the popup sends one 1-token request to the LOCAL agent endpoint (a cold model load made the first step
    # after a pause take minutes); at most once a minute; never to the cloud
    _setup(x, ["done \"ok\""])
    x.sw.evaluate("() => { tellWarmAt = 0; }")
    n = len(x.server.model_calls)
    assert x.call("tell_warm") == {"warm": True}
    _wait = time.time() + 5
    while time.time() < _wait and len(x.server.model_calls) == n:
        time.sleep(0.1)
    warm = x.server.model_calls[n:]
    assert len(warm) == 1 and warm[0]["max_tokens"] == 1 and warm[0]["model"] == "fake-agent"
    assert x.call("tell_warm") == {"warm": "recent"}                        # throttled
    x.sw.evaluate("() => { tellWarmAt = 0; }")
    x.set_settings(agentBrain="claude", cloudConsent=True)
    assert x.call("tell_warm") == {"warm": False}                           # cloud brain: nothing is sent anywhere


def test_same_click_three_times_pauses_instead_of_looping(x):
    # the real Qwen3.5-4B toggled "Marketing e-mails" for all 25 steps on this chore; two in a row is a toggle,
    # a third is a loop: it is rejected (not clicked) and the run asks the user
    _setup(x, ['click "Settings"'] + ['click "Marketing e-mails"'] * 6)
    page, _ = _start(x, "/account.html", "Turn off marketing e-mails and switch the language to Deutsch in my settings")
    t = _wait_tell(x, ("needs_user", "confirm", "ended", "awaiting_approval"))
    assert t["status"] == "needs_user" and t["pending"]["kind"] == "loop", t["feed"]
    assert "Marketing e-mails" in t["pending"]["message"]
    assert len(t["steps"]) == 4                                   # goto + Settings + two toggles; the third never ran
    assert page.is_checked("#marketing")                          # toggled twice = back where it started
    x.call("tell_stop")


def test_note_while_running_reaches_next_prompt(x):
    brain = _setup(x, ['click "Back to cart"', 'click "Back to cart"', 'done "ok"'], delay=0.8)
    _start(x, "/cart.html", "Go back to the cart")
    end = time.time() + 10
    while not brain.requests and time.time() < end:
        time.sleep(0.05)
    x.call("tell_note", text="the cart button is at the bottom")
    _wait_tell(x, ("confirm", "ended", "needs_user", "awaiting_approval"))
    assert "Note from the owner: the cart button is at the bottom" in brain.requests[1]["messages"][1]["content"]


# ---- caps ---------------------------------------------------------------------------------------------------------------

def test_step_cap(x):
    _setup(x, ['click "Back to cart"'] * 6)
    _start(x, "/cart.html", "Keep going back to the cart", caps={"steps": 2})
    t = _wait_tell(x, ("ended",))
    assert t["outcome"] == "halted" and t["reason"] == "step cap 2 reached" and t["step"] == 2


def test_time_cap(x):
    _setup(x, ['click "Back to cart"'] * 6, delay=1.2)
    _start(x, "/cart.html", "Keep going back to the cart", caps={"seconds": 1})
    t = _wait_tell(x, ("ended",))
    assert t["outcome"] == "halted" and t["reason"] == "time cap 1s reached" and t["step"] == 1


# ---- brains: local first, cloud only on opt-in, site lock wins ----------------------------------------------------------

FAKE_CLAUDE = """(replies) => {
  self.__realFetch = self.__realFetch || self.fetch;
  self.__claudeCalls = [];
  self.fetch = async function (url, init) {
    url = String(url);
    if (url.indexOf('https://api.anthropic.com/') === 0) {
      var body = JSON.parse(init.body);
      self.__claudeCalls.push({url: url, headers: init.headers, body: body});
      var text = body.system.indexOf('You operate a web dashboard') === 0 ? '# fake\\n' + replies.shift() : 'VERIFIED: fake claude';
      return new Response(JSON.stringify({content: [{type: 'text', text: text}], stop_reason: 'end_turn'}),
                          {status: 200, headers: {'content-type': 'application/json'}});
    }
    if (url.indexOf('https://') === 0) throw new Error('unexpected network call ' + url);
    return self.__realFetch(url, init);
  };
}"""


def test_cloud_only_with_consent_and_site_lock_wins(x):
    x.sw.evaluate(FAKE_CLAUDE, ['fill_enter(1, "4417")', 'done("Order 4417: Shipped")'])
    brain = _setup(x, [], agentBrain="claude")
    page = x.ctx.new_page()
    page.goto(x.server.base + "/admin.html")
    tab = x.tab_id(page)
    assert "consent" in _err(x, "tell_start", tabId=tab, goal="Look up order 4417")
    _setup(x, [], agentBrain="claude", cloudConsent=True)
    assert "API key" in _err(x, "tell_start", tabId=tab, goal="Look up order 4417")
    assert x.sw.evaluate("self.__claudeCalls.length") == 0
    x.call("save_claude_key", key=KEY)
    x.call("set_site_lock", url=x.server.base + "/admin.html")
    assert "locked off for 127.0.0.1" in _err(x, "tell_start", tabId=tab, goal="Look up order 4417")
    assert x.sw.evaluate("self.__claudeCalls.length") == 0
    x.call("set_site_lock", url=x.server.base + "/admin.html", on=False)
    x.call("tell_start", tabId=tab, goal="Look up order 4417 and tell me its status")
    t = _wait_tell(x, ("confirm", "ended", "needs_user"))
    assert t["status"] == "confirm" and t["result"]["answer"] == "Order 4417: Shipped", t["feed"]
    calls = x.sw.evaluate("self.__claudeCalls")
    agent = [c for c in calls if c["body"]["system"].startswith("You operate")]
    assert len(agent) == 2 and brain.requests == []
    b = agent[0]["body"]
    assert b["system"] == AGENT_SYSTEM + x.sw.evaluate("AWOSAgent.FORMAT_RULE") and b["model"] == "claude-haiku-5-5"
    assert b["max_tokens"] == 300 and "grammar" not in b and agent[0]["headers"]["x-api-key"] == KEY
    assert b["messages"][0]["content"].startswith("Goal: Look up order 4417")
    ep_model = t["model"]
    assert ep_model == "anthropic:claude-haiku-5-5"
    x.call("tell_stop")
    x.call("tell_clear")
    # a lock on the site mid-run: the next page is not sent
    x.sw.evaluate(FAKE_CLAUDE, ['fill_enter(1, "4417")', 'done("x")'])
    x.call("set_site_lock", site="127.0.0.1")
    assert "locked" in _err(x, "tell_start", tabId=tab, goal="Look up order 4417")
    assert x.sw.evaluate("self.__claudeCalls.length") == 0


def test_local_agent_missing_explains_without_pushing_cloud(x):
    _setup(x, [], agentEndpoint="http://127.0.0.1:9/v1")
    page = x.ctx.new_page()
    page.goto(x.server.base + "/account.html")
    tab = x.tab_id(page)
    err = _err(x, "tell_start", tabId=tab, goal="Open my settings")
    assert "llama-server" in err and "Qwen3.5-4B" in err and "Claude" not in err and "cloud" not in err.lower()
    assert "install_replay.sh --agent 4b" in err and "--agent 9b" in err             # the installer sets it up
    x.server.overrides["/v1/models"] = "models_2b.json"
    _setup(x, [])
    err = _err(x, "tell_start", tabId=tab, goal="Open my settings")
    assert "repair / verify model" in err and "Claude" not in err and "--agent 4b" in err and "8081" in err
    # the default agent endpoint is the installer's agent server (8081), not the 2B repair server (8080)
    assert x.sw.evaluate("TELL_DEFAULTS")["agentEndpoint"] == "http://127.0.0.1:8081/v1"
    assert x.sw.evaluate("TELL_DEFAULTS")["agentModel"] == "qwen3.5-4b"
    assert x.call("tell_state")["tell"] is None
    assert x.call("tell_state")["settings"]["agentBrain"] == "local"                       # local is the default


def test_recipe_cloud_lock_keeps_repair_local(x):
    """the per-recipe lock wins over the Claude brain for replay repair / verify too."""
    from test_extension_replay import _record_form
    page = x.ctx.new_page()
    rec, tab = _record_form(x, page)
    x.sw.evaluate(FAKE_CLAUDE, [])
    x.set_settings(brain="claude", cloudConsent=True)
    x.call("save_claude_key", key=KEY)
    x.call("set_recipe_cloud_lock", id=rec["id"], on=True)
    x.server.overrides["/form.html"] = "form_button_renamed.html"
    x.server.model_answer = lambda body: "PICK 1: fake"
    page.goto(x.server.base + "/help.html")
    x.call("run", recId=rec["id"], tabId=tab)
    run = x.wait_run()
    assert x.sw.evaluate("self.__claudeCalls.length") == 0
    assert any("cloud is locked off for this recipe" in l["text"] for l in run["log"]), run["log"]
    assert x.server.model_calls or x.server.verify_calls


# ---- the popup: live view, Approve, correction, confirm ---------------------------------------------------------------

def test_popup_live_view_approve_and_save(x):
    brain = _setup(x, ['fill "First name" "Ada"', 'click "Submit application"', 'done "Application received"'])
    page = x.ctx.new_page()
    page.goto(x.server.base + "/apply.html")
    tab = x.tab_id(page)
    popup = x.popup(tab)
    assert popup.get_attribute("#tellgoal", "placeholder") == "Tell it what to do…"
    assert "never leaves" in popup.inner_text("#tellbrain")
    popup.fill("#tellgoal", "Fill the application with my first name and send it")
    popup.click("#tellgo")
    popup.wait_for_selector("#tellapprove:visible", timeout=30000)
    assert "Submit application" in popup.inner_text("#tellapprove")
    assert "step 1: fill(1, \"Ada\")" in popup.inner_text("#tellfeed")
    assert page.url.endswith("/apply.html")
    popup.fill("#tellnote", "after sending, just say done")
    popup.click("#tellapprove")                                   # Approve does not carry the note; it stays typed
    popup.wait_for_selector("#tellyes:visible", timeout=30000)
    assert popup.input_value("#tellcheckurl") == x.server.base + "/applied.html"
    assert popup.input_value("#tellchecktext") == "Application received."
    popup.fill("#tellname", "Apply backend")
    popup.click("#tellyes")
    popup.wait_for_selector("#tellclose:visible", timeout=10000)
    recs = x.state()["recordings"]
    assert [r["name"] for r in recs.values()] == ["Apply backend"]
    assert len(brain.requests) == 3
