"""AWOS Replay extension, end to end in headless Chromium with the unpacked extension.

Record a 3-field form fill + submit once, then:
  - replay unchanged                  -> VERIFIED, every step re-found by scoring
  - replay after a label + id rename  -> still VERIFIED by scoring, model never asked
  - replay after the target is gone   -> stops FAILED / NOT_FOUND (model says NOT_FOUND,
                                         and separately with the model unreachable),
                                         never clicks a different control
  - approval gate on "Pay now"        -> pauses, nothing clicked until Approve is
                                         clicked in the popup; Stop -> never clicked
  - secrets                           -> password / OTP / card / SSN values never stored
Run:  PYTHONPATH=. ~/.awos/rl-venv/bin/python -m pytest tests/extension -q
"""
import json
import time

import pytest

pytestmark = pytest.mark.timeout(120)


def _record_form(x, page):
    page.goto(x.server.base + "/form.html")
    tab = x.tab_id(page)
    x.call("start_recording", tabId=tab, name="profile")
    page.fill("#fullname", "Ada Lovelace")
    page.fill("#email", "ada@example.com")
    page.select_option("#plan", "pro")
    page.click("text=Save profile")
    page.wait_for_url("**/done.html*")
    time.sleep(0.5)
    rec = x.call("stop_recording")
    rec = x.call("save_recording", id=rec["id"],
                 expect={"urlPattern": rec["expect"]["urlPattern"],
                         "texts": ["Profile saved for Ada Lovelace <ada@example.com> on plan pro"]})
    return rec, tab


def _replay(x, page, rec, tab, timeout=45):
    page.goto(x.server.base + "/help.html")       # start somewhere else; the recording's goto takes it back
    x.call("run", recId=rec["id"], tabId=tab)
    return x.wait_run(timeout=timeout)


def test_record_three_step_form(x):
    page = x.ctx.new_page()
    rec, _ = _record_form(x, page)
    acts = [s["action"] for s in rec["steps"] if s["action"] != "goto"]
    assert acts == ["fill", "fill", "select", "click"]
    assert rec["steps"][0] == {"action": "goto", "url": x.server.base + "/form.html"}
    fill = rec["steps"][1]
    assert fill["value"] == "Ada Lovelace" and fill["loc"]["label"] == "Full name" and fill["loc"]["id"] == "fullname"
    loc = rec["steps"][4]["loc"]
    for k in ("role", "name", "text", "id", "css", "neighbor", "pos", "tag"):
        assert k in loc
    assert loc["role"] == "button" and loc["name"] == "Save profile" and loc["pos"]["w"] > 0
    assert rec["steps"][4]["danger"] is None
    assert rec["expect"]["urlPattern"] == x.server.base + "/done.html"


def test_replay_unchanged_is_verified(x):
    page = x.ctx.new_page()
    rec, tab = _record_form(x, page)
    run = _replay(x, page, rec, tab)
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    assert all(r["how"].startswith("score") for r in run["resolved"]) and len(run["resolved"]) == 4
    assert "Ada Lovelace" in page.inner_text("#msg") and "plan pro" in page.inner_text("#msg")
    assert x.server.model_calls == []


def test_replay_after_label_rename_refinds(x):
    page = x.ctx.new_page()
    rec, tab = _record_form(x, page)
    x.server.overrides["/form.html"] = "form_renamed.html"
    run = _replay(x, page, rec, tab)
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    email = next(r for r in run["resolved"] if r["step"] == 2)
    assert email["name"] == "Work e-mail address" and email["how"].startswith("score")
    assert "<ada@example.com>" in page.inner_text("#msg")
    assert x.server.model_calls == []


def test_replay_after_target_deleted_stops_not_found(x):
    page = x.ctx.new_page()
    rec, tab = _record_form(x, page)
    x.server.overrides["/form.html"] = "form_deleted.html"
    x.server.model_answer = "NOT_FOUND"
    run = _replay(x, page, rec, tab)
    assert run["status"] == "failed" and run["result"]["verdict"] == "FAILED"
    assert run["result"]["code"] == "NOT_FOUND" and "step 5" in run["result"]["reason"], run["result"]
    assert len(x.server.model_calls) == 1                     # the shortlist went to the local model
    prompt = x.server.model_calls[0]["messages"][1]["content"]
    assert "Save profile" in prompt and "Cancel" in prompt
    assert page.url.startswith(x.server.base + "/form.html")  # did not click Cancel / Help instead
    assert "stopped" in page.inner_text("#__awos_banner")


def test_replay_target_deleted_model_unreachable_stops(x):
    page = x.ctx.new_page()
    rec, tab = _record_form(x, page)
    x.server.overrides["/form.html"] = "form_deleted.html"
    x.set_settings(endpoint="http://127.0.0.1:9/v1")          # nothing listens there
    run = _replay(x, page, rec, tab)
    assert run["result"]["code"] == "NOT_FOUND" and "unreachable" in run["result"]["reason"]
    assert page.url.startswith(x.server.base + "/form.html")


def test_model_out_of_range_answer_is_not_found(x):
    page = x.ctx.new_page()
    rec, tab = _record_form(x, page)
    x.server.overrides["/form.html"] = "form_deleted.html"
    x.server.model_answer = "99"                              # out of range -> treated as NOT_FOUND
    run = _replay(x, page, rec, tab)
    assert run["result"]["code"] == "NOT_FOUND"


def _pick_line_with(word):
    """fake model (repair contract): PICK the candidate line mentioning `word`, else NOT_FOUND."""
    def answer(body):
        for line in body["messages"][1]["content"].split("Candidates on the page now:\n")[1].splitlines():
            if word in line and line[:1].isdigit():
                return f"PICK {line.split('.')[0]}: fake"
        return "NOT_FOUND: fake"
    return answer


def test_model_pick_used_when_scoring_unanchored(x):
    """Save button renamed "Update" with a new id: no identity attribute matches, so
    scoring will not claim it; the local model picks it from the shortlist."""
    page = x.ctx.new_page()
    rec, tab = _record_form(x, page)
    x.server.overrides["/form.html"] = "form_button_renamed.html"
    x.server.model_answer = _pick_line_with("named 'Update'")
    run = _replay(x, page, rec, tab)
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    click = next(r for r in run["resolved"] if r["step"] == 4)
    assert click["how"].startswith("model pick") and click["name"] == "Update"
    assert len(x.server.model_calls) == 1


def test_model_call_speaks_repair_contract_and_export_is_redacted(x):
    """The model gets the trained repair prompt (G6's format, with region / nearby text / siblings), the
    run records the call, and the shareable log carries it without typed values or URL queries."""
    page = x.ctx.new_page()
    rec, tab = _record_form(x, page)
    x.server.overrides["/form.html"] = "form_button_renamed.html"
    x.server.model_answer = _pick_line_with("named 'Update'")
    run = _replay(x, page, rec, tab)
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    body = x.server.model_calls[0]
    assert body["messages"][1]["content"].startswith("The step used: button named 'Save profile'")
    assert "between " in body["messages"][1]["content"] and body["grammar"].startswith('root ::= ("PICK "')
    log = x.call("export_log")
    blob = json.dumps(log)
    assert "Ada Lovelace" not in blob and "ada@example.com" not in blob     # typed values never leave
    r = next(r for r in log["runs"] if r["recName"] == "profile" and r["modelCalls"])
    assert r["verdict"] == "VERIFIED" and r["modelCalls"][0]["outcome"] == "PICK " + r["modelCalls"][0]["answer"][5]
    steps = next(x_ for x_ in log["recordings"] if x_["id"] == rec["id"])["steps"]
    assert any(s.get("valueLen") == len("Ada Lovelace") for s in steps)
    assert all("?" not in (s.get("url") or "") for s in steps)


def test_verifier_may_only_downgrade(x):
    """Checks pass + verifier says NOT_DONE -> CHECK (with its reason); verifier VERIFIED or unparsable ->
    the checks' verdict stands; a FAILED run is never upgraded. The verifier sees the goal and the page."""
    page = x.ctx.new_page()
    rec, tab = _record_form(x, page)
    x.call("save_recording", id=rec["id"], goal="Profile saved for Ada on plan pro", expect=rec["expect"])
    x.server.verify_answer = "NOT_DONE: the plan shown is not pro"
    run = _replay(x, page, rec, tab)
    assert run["result"]["verdict"] == "CHECK" and "not pro" in run["result"]["reason"], run["log"]
    user = x.server.verify_calls[-1]["messages"][1]["content"]
    assert user.startswith("Goal: Profile saved for Ada on plan pro\nActions taken (")
    assert '\n<observation untrusted="true">\nURL path: /done.html  (HTTP 200)\n' in user and user.endswith("Was the goal really completed?")
    assert 'fill(' in user and 'on textbox "' in user
    assert f'Current address: {x.server.base}/done.html\n' in user                  # the replay's own evidence
    assert 'Replay checks (recorded with the chore, run on this page): address is ' in user and 'on plan pro": PASS' in user

    x.server.verify_answer = "maybe"                                  # unparsable: never changes the verdict
    run = _replay(x, page, rec, tab)
    assert run["result"]["verdict"] == "VERIFIED" and run["verifier"].get("error"), run["log"]

    x.call("save_recording", id=rec["id"], goal="g", expect={"urlPattern": rec["expect"]["urlPattern"], "texts": ["not on the page"]})
    x.server.verify_answer = "VERIFIED: looks fine"                   # a failed check stays FAILED
    run = _replay(x, page, rec, tab)
    assert run["result"]["verdict"] == "FAILED" and run["verifier"]["label"] == "VERIFIED"
    log = x.call("export_log")
    assert any(r.get("verifier") and r["verifier"]["label"] == "NOT_DONE" for r in log["runs"])


def test_gate_applies_to_model_picked_target(x):
    """"Pay now" renamed "Complete payment": the model re-finds it, and the gate still pauses."""
    page = x.ctx.new_page()
    rec, tab = _record_checkout(x, page)
    x.server.overrides["/cart.html"] = "cart_renamed.html"
    x.server.model_answer = _pick_line_with("Complete payment")
    page.goto(x.server.base + "/help.html")
    x.call("run", recId=rec["id"], tabId=tab)
    run = x.wait_run(statuses=("awaiting_approval", "done", "failed"))
    assert run["status"] == "awaiting_approval", run["log"]
    assert "Complete payment" in run["pending"]["message"]
    assert page.inner_text("#status") == "Not paid"
    x.call("reject", runId=run["id"], nonce=run["pending"]["nonce"])


def test_non_loopback_endpoint_refused(x):
    page = x.ctx.new_page()
    rec, tab = _record_form(x, page)
    x.server.overrides["/form.html"] = "form_deleted.html"
    x.set_settings(endpoint="https://api.example.com/v1")
    run = _replay(x, page, rec, tab)
    assert "not loopback" in run["result"]["reason"]


def _record_checkout(x, page):
    page.goto(x.server.base + "/cart.html")
    tab = x.tab_id(page)
    x.call("start_recording", tabId=tab, name="pay")
    page.fill("#note", "order 7")
    page.click("#pay")
    time.sleep(0.5)
    rec = x.call("stop_recording")
    rec = x.call("save_recording", id=rec["id"], expect={"urlPattern": rec["expect"]["urlPattern"],
                                                         "texts": ["Payment sent: order 7"]})
    return rec, tab


def test_approval_gate_pauses_on_pay_now_until_popup_click(x):
    page = x.ctx.new_page()
    rec, tab = _record_checkout(x, page)
    assert rec["steps"][-1]["danger"] == "pay now"
    page.goto(x.server.base + "/help.html")
    x.call("run", recId=rec["id"], tabId=tab)
    run = x.wait_run(statuses=("awaiting_approval", "done", "failed"))
    assert run["status"] == "awaiting_approval" and "Pay now" in run["pending"]["message"]
    time.sleep(2.0)                                           # paused means paused: nothing auto-approves
    assert x.state()["run"]["status"] == "awaiting_approval"
    assert page.inner_text("#status") == "Not paid"
    assert page.input_value("#note") == "order 7"             # the step before the gate did run

    popup = x.popup(tab)
    popup.wait_for_selector("#approve:not([hidden])")
    popup.click("#approve")
    run = x.wait_run()
    assert run["result"]["verdict"] == "VERIFIED", run["log"]
    assert page.inner_text("#status") == "Payment sent: order 7"
    assert any("user approved" in l["text"] for l in run["log"])
    popup.wait_for_selector("#verdict.VERIFIED")


def test_approval_gate_stop_never_clicks(x):
    page = x.ctx.new_page()
    rec, tab = _record_checkout(x, page)
    page.goto(x.server.base + "/help.html")
    x.call("run", recId=rec["id"], tabId=tab)
    x.wait_run(statuses=("awaiting_approval",))
    popup = x.popup(tab)
    popup.wait_for_selector("#reject")
    popup.click("#reject")
    run = x.wait_run()
    assert run["result"]["verdict"] == "FAILED" and run["result"]["code"] == "REJECTED"
    assert page.inner_text("#status") == "Not paid"


def test_approve_with_wrong_nonce_is_refused(x):
    page = x.ctx.new_page()
    rec, tab = _record_checkout(x, page)
    page.goto(x.server.base + "/help.html")
    x.call("run", recId=rec["id"], tabId=tab)
    run = x.wait_run(statuses=("awaiting_approval",))
    r = x.sw.evaluate("([id]) => HANDLERS.approve({runId: id, nonce: 'guess'}).then(() => 'ok', e => String(e))", [run["id"]])
    assert "stale" in r
    assert x.state()["run"]["status"] == "awaiting_approval" and page.inner_text("#status") == "Not paid"
    # approve/run/... are refused from a web-page sender; only rec_step/hello are page messages
    assert x.sw.evaluate("fromExtensionPage({id: chrome.runtime.id, url: 'http://127.0.0.1/x'})") is False
    assert x.sw.evaluate("fromExtensionPage({id: chrome.runtime.id, url: chrome.runtime.getURL('popup.html')})") is True
    x.call("reject", runId=run["id"], nonce=run["pending"]["nonce"])


def test_secrets_never_recorded_and_replay_asks_user(x):
    page = x.ctx.new_page()
    page.goto(x.server.base + "/login.html")
    tab = x.tab_id(page)
    x.call("start_recording", tabId=tab, name="login")
    secrets = {"#pw": "hunter2-SECRET", "#code": "424242", "#cardno": "4111111111111111", "#ssn": "078-05-1120"}
    page.fill("#user", "ada")
    for sel, v in secrets.items():
        page.fill(sel, v)
    page.click("#next")
    time.sleep(0.5)
    rec = x.call("stop_recording")
    blob = json.dumps(x.sw.evaluate("chrome.storage.local.get(null)")) + json.dumps(x.state())
    for v in secrets.values():
        assert v not in blob
    fills = [s for s in rec["steps"] if s["action"] == "fill"]
    assert fills[0]["value"] == "ada" and fills[0]["sensitive"] is None
    assert len(fills) == 5 and fills[1]["value"] == "{{secret:password}}"
    assert all(s["value"].startswith("{{secret:") and s["sensitive"] for s in fills[1:]), fills
    # replay pauses for the user at the first secret instead of typing anything
    page.goto(x.server.base + "/help.html")
    x.call("run", recId=rec["id"], tabId=tab)
    run = x.wait_run(statuses=("needs_user", "done", "failed"))
    assert run["status"] == "needs_user" and "password" in run["pending"]["message"]
    assert page.input_value("#user") == "ada" and page.input_value("#pw") == ""
    x.call("reject", runId=run["id"], nonce=run["pending"]["nonce"])


def test_popup_lists_recordings_and_runs(x):
    page = x.ctx.new_page()
    rec, tab = _record_form(x, page)
    page.goto(x.server.base + "/help.html")
    popup = x.popup(tab)
    popup.wait_for_selector(f"li[data-rec='{rec['id']}']")
    assert "4 actions" in popup.inner_text(f"li[data-rec='{rec['id']}']")
    popup.click(f"li[data-rec='{rec['id']}'] button.run")
    run = x.wait_run()
    assert run["result"]["verdict"] == "VERIFIED"
    popup.wait_for_selector("#verdict.VERIFIED")


def _record_spa_delete(x, page):
    """A span with an addEventListener handler is not a recordable click, so the
    recorder sees only the navigation it causes (/account_delete.html?confirm=1)."""
    page.goto(x.server.base + "/spa.html")
    tab = x.tab_id(page)
    x.call("start_recording", tabId=tab, name="spa")
    page.click("#danger-zone")
    page.wait_for_url("**/account_delete.html*")
    time.sleep(0.5)
    return x.call("stop_recording"), tab


def test_recorded_navigation_to_dangerous_url_is_gated(x):
    """Replaying that goto is the delete itself: it must pause for approval like a click."""
    page = x.ctx.new_page()
    rec, tab = _record_spa_delete(x, page)
    assert [s["action"] for s in rec["steps"]] == ["goto", "goto"], rec["steps"]
    assert "account_delete.html?confirm=1" in rec["steps"][1]["url"]
    page.goto(x.server.base + "/help.html")
    x.call("run", recId=rec["id"], tabId=tab)
    run = x.wait_run(statuses=("awaiting_approval", "done", "failed"))
    assert run["status"] == "awaiting_approval" and run["pending"]["step"] == 1, run["log"]
    assert "account_delete.html?confirm=1" in run["pending"]["message"] and run["pending"]["danger"] == "delete"
    time.sleep(1.5)
    assert x.state()["run"]["status"] == "awaiting_approval"
    assert page.url.startswith(x.server.base + "/spa.html")    # the dangerous URL was never opened
    x.call("approve", runId=run["id"], nonce=run["pending"]["nonce"])
    run = x.wait_run()
    assert run["status"] == "done", run["log"]
    assert page.url.startswith(x.server.base + "/account_delete.html")


def test_dangerous_goto_reject_never_navigates(x):
    page = x.ctx.new_page()
    rec, tab = _record_spa_delete(x, page)
    page.goto(x.server.base + "/help.html")
    x.call("run", recId=rec["id"], tabId=tab)
    run = x.wait_run(statuses=("awaiting_approval",))
    x.call("reject", runId=run["id"], nonce=run["pending"]["nonce"])
    run = x.wait_run()
    assert run["result"]["code"] == "REJECTED"
    assert page.url.startswith(x.server.base + "/spa.html")


def test_approval_completes_when_live_target_has_no_name(x):
    """Recorded "Pay now"; at replay the button is icon-only (re-found by its id).
    One Approve must be enough: the pause and the approval check name the target
    the same way, so the run does not re-pause on every Approve."""
    page = x.ctx.new_page()
    rec, tab = _record_checkout(x, page)
    x.server.overrides["/cart.html"] = "cart_icon.html"
    page.goto(x.server.base + "/help.html")
    x.call("run", recId=rec["id"], tabId=tab)
    run = x.wait_run(statuses=("awaiting_approval", "done", "failed"))
    assert run["status"] == "awaiting_approval", run["log"]
    assert page.inner_text("#status") == "Not paid"
    x.call("approve", runId=run["id"], nonce=run["pending"]["nonce"])
    run = x.wait_run(statuses=("awaiting_approval", "done", "failed"))
    assert run["status"] == "done" and run["result"]["verdict"] == "VERIFIED", run["log"]
    assert page.inner_text("#status") == "Payment sent: order 7"
    assert sum("paused" in l["text"] for l in run["log"]) == 1


def test_run_refused_while_recording(x):
    page = x.ctx.new_page()
    rec, tab = _record_form(x, page)
    x.call("start_recording", tabId=tab, name="again")
    r = x.sw.evaluate("([id, t]) => HANDLERS.run({recId: id, tabId: t}).then(() => 'ok', e => String(e))", [rec["id"], tab])
    assert "stop the recording" in r
    x.call("stop_recording")
