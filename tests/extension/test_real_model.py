"""End to end with a REAL local model behind the extension (CI: .github/workflows/ext-real-model.yml).

Skipped unless AWOS_REAL_MODEL_URL points at a llama-server (e.g. http://127.0.0.1:8080/v1) serving the
repair model (G6). Same recordings and page variants as test_extension_replay.py, but the shortlist
decision is the model's: a renamed button must be picked, a deleted one must stop the run (NOT_FOUND),
and a model-picked "pay" control must still pause at the approval gate.
"""
import os

import pytest

from test_extension_replay import _record_checkout, _record_form, _replay

URL = os.environ.get("AWOS_REAL_MODEL_URL", "")
pytestmark = [pytest.mark.timeout(240), pytest.mark.skipif(not URL, reason="AWOS_REAL_MODEL_URL not set")]


@pytest.fixture()
def xr(x):
    x.set_settings(endpoint=URL, model="local", timeoutMs=120000, verifyTimeoutMs=180000)
    return x


def _model_lines(run):
    return [l["text"] for l in run["log"] if "model" in l["text"]]


def test_real_model_picks_renamed_button(xr):
    page = xr.ctx.new_page()
    rec, tab = _record_form(xr, page)
    xr.server.overrides["/form.html"] = "form_button_renamed.html"
    run = _replay(xr, page, rec, tab, timeout=200)
    print("model:", run.get("modelCalls"), _model_lines(run))
    assert run["result"]["verdict"] == "VERIFIED", run["log"]


def test_real_model_refuses_deleted_button(xr):
    page = xr.ctx.new_page()
    rec, tab = _record_form(xr, page)
    xr.server.overrides["/form.html"] = "form_deleted.html"
    run = _replay(xr, page, rec, tab, timeout=200)
    print("model:", run.get("modelCalls"), _model_lines(run))
    assert run["status"] == "failed" and run["result"]["code"] == "NOT_FOUND", run["log"]


def _verify_case(xr, done_page):
    page = xr.ctx.new_page()
    rec, tab = _record_form(xr, page)
    xr.call("save_recording", id=rec["id"], goal="Save the profile for Ada Lovelace (ada@example.com) on plan pro",
            expect={"urlPattern": rec["expect"]["urlPattern"], "texts": ["Profile saved"]})   # passes on both pages
    if done_page:
        xr.server.overrides["/done.html"] = done_page
    run = _replay(xr, page, rec, tab, timeout=200)
    print("verifier:", run.get("verifier"), "| verdict:", run["result"]["verdict"])
    return run


def test_real_verifier_keeps_a_good_run(xr):
    run = _verify_case(xr, None)
    assert run["result"]["verdict"] == "VERIFIED", run["log"]


def test_real_verifier_flags_a_lying_banner(xr):
    run = _verify_case(xr, "done_wrong.html")          # "Profile saved ... on plan basic": checks pass, detail wrong
    assert run["result"]["verdict"] == "CHECK", run["log"]


def test_real_model_pick_still_gated(xr):
    page = xr.ctx.new_page()
    rec, tab = _record_checkout(xr, page)
    xr.server.overrides["/cart.html"] = "cart_renamed.html"
    page.goto(xr.server.base + "/help.html")
    xr.call("run", recId=rec["id"], tabId=tab)
    run = xr.wait_run(statuses=("awaiting_approval", "done", "failed"), timeout=200)
    print("model:", run.get("modelCalls"), _model_lines(run))
    assert run["status"] in ("awaiting_approval", "failed"), run["log"]   # never clicks pay on its own
    if run["status"] == "awaiting_approval":
        xr.call("reject", runId=run["id"], nonce=run["pending"]["nonce"])
