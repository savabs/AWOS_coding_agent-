"""AWOS Replay v0.10 learning from every run (docs/research/experience_reuse_2026-11-05.md), end to end in the extension.

  - self-healing steps (lib/heal.js): the invoices portal is redesigned (recorded link "Invoice September 2026",
    now buttons "Oct-2026 Invoice"); the goal-guided model rescues the step, the run is VERIFIED -> the model's control
    is written back as a verified, period-templated ALTERNATE of that step (the recorded loc is untouched); the NEXT
    run (November) resolves the step with that alternate and no model call; after 2 misses the recorded control is
    demoted; the popup's "Undo fix" removes the alternates and the model is asked again
  - an alternate that is not found twice in a row loses its promotion (the recording is tried first again)
  - lessons (lib/lessons.js): a correction typed in Tell it is offered as "Remember for <site>?" in the popup;
    remembered, it is in the next Tell-it prompt (2 rules at most); a stop by the owner does not score it; two
    episodes the owner says are not done switch it off
  - a correction after a failed replay -> the chip in the run panel -> the next goal-guided prompt has the rule; the
    VERIFIED run scores it
  - the site map (lib/sitemap.js): a recording is merged as 'seen', a VERIFIED replay makes its path 'ok', a failed
    replay marks the step it stopped at 'fail'; the run-log export carries the heal / lesson / site-map counts
Run:  PYTHONPATH=. ~/.awos/rl-venv/bin/python -m pytest tests/extension/test_learning.py -q
"""
import time

import pytest

from test_goal_guided import _gen_hits, _record_september, _run as _grun, _wait
from test_realportal import HOME, _full_route, _logs, _record, _run as _prun
from test_tell_it import Brain, _setup as _tell_setup, _tell, _wait_tell

pytestmark = pytest.mark.timeout(300)


def _rec(x, rid):
    return x.state()["recordings"][rid]


def _cands(x, step):
    return x.sw.evaluate("(s) => AWOSHeal.candidates(s).map(c => c.source)", step)


def _edges(x, host="127.0.0.1"):
    m = x.sw.evaluate("getLocal('siteMap', {hosts: {}})")
    return {(e["control"]["role"], e["control"]["name"]): e for e in ((m.get("hosts") or {}).get(host) or {}).get("edges", {}).values()}


def _lessons(x):
    return x.sw.evaluate("getLocal('lessons', [])")


# ---- 1. a model rescue becomes a verified alternate; the next run needs no model; demotion; Undo fix -----------------

def test_guided_rescue_is_learned_next_run_has_no_model_call_primary_demoted_and_undo(x):
    page, tab, rec, k = _record_september(x)
    x.call("save_recording", id=rec["id"], goal="Download the September 2026 supplier invoice")
    brain = Brain(['click "Oct-2026 Invoice"'])
    x.server.model_answer = brain
    x.server.state.update(latest="2026-10", layout="cards")          # the portal was redesigned
    run = _grun(x, page, rec, tab)
    assert run["result"]["verdict"] == "VERIFIED", _logs(run)
    assert len(brain.requests) == 1
    step = _rec(x, rec["id"])["steps"][k]
    assert step["loc"]["name"] == "Invoice September 2026", step["loc"]       # the recording is never overwritten
    alts = step["alternates"]
    assert len(alts) == 1 and alts[0]["verified"] and alts[0]["by"] == "4b" and alts[0]["runId"] == run["id"], alts
    assert alts[0]["loc"]["role"] == "button" and alts[0]["loc"]["name"] == "{{month_short}}-{{year}} Invoice", alts[0]["loc"]
    assert "value" not in alts[0]["loc"] and step.get("primaryFails") == 1, step
    assert any(t.startswith("fixed: step %d now also matches button \"{{month_short}}-{{year}} Invoice\" (fixed by the local model" % (k + 1))
               and "verified" in t for t in _logs(run)), _logs(run)

    # the next month: the learned control is tried after the recorded one misses; no model call at all
    x.server.state.update(latest="2026-11", layout="cards")
    run = _grun(x, page, rec, tab, period="2026-11")
    assert run["result"]["verdict"] == "VERIFIED", _logs(run)
    assert len(brain.requests) == 1 and x.server.model_calls == [], x.server.model_calls
    assert _gen_hits(x) == ["/files/gen/invoice-2026-11.pdf"], x.server.hits
    assert any("the recorded control was not found; used a learned fix: step %d now also matches button" % (k + 1) in t for t in _logs(run)), _logs(run)
    assert not run.get("guided") and run["heal"][str(k)]["source"] == "alternate", run.get("heal")
    step = _rec(x, rec["id"])["steps"][k]
    assert step["alternates"][0]["ok"] == 1 and step["primaryFails"] == 2, step
    assert _cands(x, step) == ["alternate", "primary"]               # 2 misses + a verified alternate: the recording steps back

    # the run log export: counts and role / name only
    log = x.call("export_log")
    h = next(r for r in log["recordings"] if r["id"] == rec["id"])["heals"]
    assert h["steps"] == 1 and h["alternates"] == 1 and h["verified"] == 1 and h["demotedPrimary"] == 1, h
    assert h["fixes"] == [{"step": k, "role": "button", "name": "{{month_short}}-{{year}} Invoice", "by": "4b", "verified": True, "ok": 1, "fail": 0}]
    assert log["runs"][0]["heal"] == [{"step": k, "used": "alternate", "tried": 2, "overridden": False}], log["runs"][0]["heal"]
    assert log["siteMap"]["127.0.0.1"]["edges"] >= 1

    # Undo fix in the popup: the step has only its recording again, so the model is asked again
    popup = x.popup(tab)
    li = "li[data-rec='%s']" % rec["id"]
    popup.click(li + " summary:has-text('Learned fixes (1)')")
    assert 'now also matches button "{{month_short}}-{{year}} Invoice"' in popup.inner_text(li + " .fixrow")
    popup.click(li + " .undofix")
    _wait(lambda: not _rec(x, rec["id"])["steps"][k].get("alternates"), what="the fix undone")
    step = _rec(x, rec["id"])["steps"][k]
    assert "primaryFails" not in step and step["loc"]["name"] == "Invoice September 2026", step
    brain2 = Brain(['click "Dec-2026 Invoice"'])
    x.server.model_answer = brain2
    x.server.state.update(latest="2026-12", layout="cards")
    run = _grun(x, page, rec, tab, period="2026-12")
    assert run["result"]["verdict"] == "VERIFIED" and len(brain2.requests) == 1, _logs(run)


# ---- 2. an alternate that misses twice in a row loses its promotion -----------------------------------------------------

def test_promoted_alternate_missing_twice_is_demoted_behind_the_recording(x):
    page, tab, rec, k = _record_september(x)
    gone = {"key": "alt:button|archived statements|", "loc": {"tag": "button", "role": "button", "name": "Archived statements", "text": "Archived statements"},
            "by": "4b", "ok": 2, "fail": 0, "streak": 0, "verified": True, "at": 1, "runId": "run-old"}
    x.sw.evaluate("""([id, k, alt]) => getRec(id).then(r => { r.steps[k].alternates = [alt]; return putRec(r); })""", [rec["id"], k, gone])
    assert _cands(x, _rec(x, rec["id"])["steps"][k]) == ["alternate", "primary"]
    x.server.state.update(latest="2026-10")                         # the table layout: the recorded link is there
    for n in (1, 2):
        run = _grun(x, page, rec, tab)
        assert run["result"]["verdict"] == "VERIFIED" and x.server.model_calls == [], _logs(run)
        a = _rec(x, rec["id"])["steps"][k]["alternates"][0]
        assert a["fail"] == n and a["streak"] == n and a["ok"] == 2, a
    step = _rec(x, rec["id"])["steps"][k]
    assert _cands(x, step) == ["primary", "alternate"] and "primaryFails" not in step, step


# ---- 3. a Tell-it correction -> Remember -> next prompt (2 rules at most) -> a failing streak switches it off ----------

def _rules(prompt):
    return [l for l in prompt.split("\n") if l.startswith("- When ")]


def test_tell_it_correction_remembered_in_next_prompt_and_failing_streak_disables_it(x):
    _tell_setup(x, ['needs_user "Which menu holds billing?"'])
    page = x.ctx.new_page()
    page.goto(x.server.base + HOME)
    tab = x.tab_id(page)
    x.call("tell_start", tabId=tab, goal="open my billing page", explore=True)
    _wait_tell(x, ("needs_user",))
    r = x.call("tell_note", text="use the Settings tab, not Profile")
    assert r["offer"]["host"] == "127.0.0.1" and r["offer"]["from"] == "tell", r
    popup = x.popup(tab)
    popup.wait_for_selector("#tellchipat #lessonchip:not([hidden])")
    assert "Remember for 127.0.0.1?" in popup.inner_text("#lessonchipmsg")
    popup.click("#lessonyes")
    _wait(lambda: _lessons(x), what="the lesson")
    mine = _lessons(x)[0]
    assert (mine["do"], mine["avoid"], mine["src"], mine["host"]) == ("Settings tab", "Profile", "user", "127.0.0.1"), mine
    assert mine["state"] == "127.0.0.1/app/home.html", mine
    popup.wait_for_selector("#lessonchip", state="hidden")
    popup.wait_for_selector("#lessonsec:not([hidden])")
    popup.click("#lessonsum")
    assert "avoid Profile" in popup.inner_text("#lessons") and "127.0.0.1" in popup.inner_text("#lessons")
    x.call("tell_stop")
    assert _lessons(x)[0]["hits"] == 0                                # it was learned after this episode's prompt

    # two more rules on the site: still only 2 go into a prompt, the one for this page first
    x.sw.evaluate("""() => getLocal('lessons', []).then(l => {
        ['never click Customize', 'click Chats and tasks instead of Projects'].forEach((t, i) =>
          l = AWOSLessons.add(l, AWOSLessons.fromCorrection(t, {host: '127.0.0.1', stateKey: '127.0.0.1/other', goal: 'chat', now: Date.now() + i})));
        return setLocal('lessons', l); })""")
    # a stop by the owner says nothing about the rules (v0.10 review): told, but not scored
    brain = _tell_setup(x, ['needs_user "Not sure"'])
    x.call("tell_clear")
    x.call("tell_start", tabId=tab, goal="open my billing page", explore=True)
    _wait_tell(x, ("needs_user",))
    assert "do Settings tab. Avoid: Profile." in brain.requests[0]["messages"][1]["content"]
    x.call("tell_stop")
    stopped = next(l for l in _lessons(x) if l["id"] == mine["id"])
    assert not stopped.get("disabled") and not stopped.get("losses") and not stopped.get("hits"), stopped
    for n in (1, 2):                                                  # two episodes the owner says are not done
        brain = _tell_setup(x, ['done "Billing is open"'])
        x.call("tell_clear")
        x.call("tell_start", tabId=tab, goal="open my billing page", explore=True)
        t = _wait_tell(x, ("confirm",))
        p = brain.requests[0]["messages"][1]["content"]
        assert "Rules learned on this site (from earlier runs):" in p and len(_rules(p)) == 2, _rules(p)
        assert "- When on " in p and "do Settings tab. Avoid: Profile." in p, _rules(p)
        x.call("tell_confirm", nonce=t["pending"]["nonce"], yes=False)
        assert _tell(x)["status"] == "ended"
    mine = next(l for l in _lessons(x) if l["id"] == mine["id"])
    assert mine["disabled"] and mine["losses"] == 2 and mine["why"] == "failed 2 runs in a row", mine
    brain = _tell_setup(x, ['needs_user "Not sure"'])
    x.call("tell_clear")
    x.call("tell_start", tabId=tab, goal="open my billing page", explore=True)
    _wait_tell(x, ("needs_user",))
    p = brain.requests[0]["messages"][1]["content"]
    assert "Avoid: Profile" not in p and len(_rules(p)) <= 2, _rules(p)
    x.call("tell_stop")
    log = x.call("export_log")
    assert log["tellit"][1]["lessons"] and log["lessons"]["127.0.0.1"]["lessons"] == 3, log["lessons"]


# ---- 4. a correction after a failed replay -> the next goal-guided prompt; the site map's 'fail' ------------------------

def test_failed_replay_correction_is_offered_used_by_the_model_and_scored(x):
    page, tab, rec, k = _record_september(x)
    name = ("link", "Invoice September 2026")
    e = _edges(x)[name]
    assert e["seen"] == 1 and e["ok"] == 0 and e["effect"]["kind"] == "downloaded", e    # the recording: 'seen'
    x.server.state.update(latest="2026-10", layout="cards")
    run = _grun(x, page, rec, tab)                                   # the fake model has no answer: FAILED at the step
    assert run["result"]["verdict"] == "FAILED" and run["i"] == k, _logs(run)
    assert _edges(x)[name]["fail"] == 1
    popup = x.popup(tab)
    popup.wait_for_selector("#runnotebar:not([hidden])")
    popup.fill("#runnote", "click the Oct-2026 Invoice button, not the Help link")
    popup.click("#runnotego")
    popup.wait_for_selector("#runchipat #lessonchip:not([hidden])")
    assert "Remember for 127.0.0.1?" in popup.inner_text("#lessonchipmsg")
    popup.click("#lessonyes")
    _wait(lambda: _lessons(x), what="the lesson")
    assert _lessons(x)[0]["avoid"] == "Help link", _lessons(x)

    brain = Brain(['click "Oct-2026 Invoice"'])
    x.server.model_answer = brain
    run = _grun(x, page, rec, tab)
    assert run["result"]["verdict"] == "VERIFIED", _logs(run)
    p = brain.requests[0]["messages"][1]["content"]
    assert "Rules learned on this site (from earlier runs):" in p and "Avoid: Help link." in p, p
    assert run["lessonIds"] == [_lessons(x)[0]["id"]] and [m["lessons"] for m in run["modelCalls"] if m.get("kind") == "guided"] == [1]
    les = _lessons(x)[0]
    assert les["hits"] == 1 and les["wins"] == 1 and not les["disabled"], les
    assert x.call("export_log")["runs"][0]["lessons"] == [les["id"]]


# ---- 5. the site map grows from a recording and from a VERIFIED replay (claude-like app) --------------------------------

def test_site_map_grows_from_recording_then_verified_replay(x):
    page, tab, rec, _ = _record(x, _full_route)
    es = _edges(x)
    keys = [("button", "S Saurabh · Max"), ("menuitem", "Settings"), ("tab", "Billing"), ("link", "Download PDF")]
    assert all(es[k]["seen"] == 1 and es[k]["ok"] == 0 for k in keys), es
    assert x.sw.evaluate("getLocal('siteMapRecs', {})").get(rec["id"]) == 1     # merged once, when it was saved
    run = _prun(x, page, tab, rec)
    assert run["result"]["verdict"] == "VERIFIED", _logs(run)
    es = _edges(x)
    assert all(es[k]["ok"] == 1 and es[k]["seen"] == 2 for k in keys), es
    assert not any("value" in e["control"] for e in es.values())
    sm = x.call("export_log")["siteMap"]["127.0.0.1"]
    assert sm["edges"] == len(es) and sm["states"] >= 4, sm
