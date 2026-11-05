"""Goal-guided steps and pop-up reading with a REAL local agent model (no scripted brain).

The scripted tests (test_goal_guided.py, test_portal_p0.py) prove the plumbing; this measures the decisions:
  - redesigned portal ("Oct-2026 Invoice" buttons): does the model pick October?
  - the wanted month is not posted (list or card layout): does it say so (MISSING) instead of clicking another month?
  - a survey pop-up no rule closes: does it read it as harmless and name the close control?
Skipped unless AWOS_REAL_AGENT_URL points at a llama-server serving the agent model (e.g. the installer's :8081):
  AWOS_REAL_AGENT_URL=http://127.0.0.1:8081/v1 PYTHONPATH=. ~/.awos/rl-venv/bin/python -m pytest tests/extension -q -s -k real_decision
Report-only: each case prints a REAL-DECISION line (ok, verdict, what the model did, seconds) and never fails on a
wrong decision; results go to $AWOS_REAL_DECISIONS_OUT when set.
"""
import json
import os
import time

import pytest

from test_goal_guided import _gen_hits, _record_september
from test_goal_guided import _run as _run_guided
from test_portal_p0 import _record_payouts
from test_portal_p0 import _run as _run_p0

URL = os.environ.get("AWOS_REAL_AGENT_URL", "")
MODEL = os.environ.get("AWOS_REAL_AGENT_MODEL", "qwen3.5-4b")
pytestmark = [pytest.mark.timeout(600), pytest.mark.skipif(not URL, reason="AWOS_REAL_AGENT_URL not set")]
RESULTS = []


def _real(x):
    x.server.model_answer = "NOT_FOUND"          # 2B repair / verify calls stay on the fake server; the agent uses URL
    x.set_settings(agentEndpoint=URL, agentModel=MODEL)


def _end(x, run):
    """release a paused run so the next case can start (reject = the user's Stop)."""
    if run.get("status") in ("needs_user", "awaiting_approval") and run.get("pending", {}).get("nonce"):
        try:
            x.call("reject", runId=run["id"], nonce=run["pending"]["nonce"])
        except Exception:
            pass


def _report(case, ok, run, t0, extra=None):
    res = run.get("result") or {}
    row = {"case": case, "ok": bool(ok), "status": run.get("status"), "verdict": res.get("verdict"),
           "reason": (res.get("reason") or (run.get("pending") or {}).get("message") or "")[:200],
           "guided": [{"control": g.get("control"), "why": (g.get("why") or "")[:120]} for g in run.get("guided") or []],
           "popups": [{k: p.get(k) for k in ("model", "verdict", "control", "modelReason")} for p in run.get("popups") or []],
           "seconds": round(time.time() - t0, 1)}
    row.update(extra or {})
    RESULTS.append(row)
    print("\nREAL-DECISION", json.dumps(row))
    out = os.environ.get("AWOS_REAL_DECISIONS_OUT")
    if out:
        with open(out, "w") as fh:
            json.dump(RESULTS, fh, indent=1)


@pytest.mark.parametrize("layout", ["list", "cards"])
def test_real_decision_month_not_posted_is_missing(x, layout):
    page, tab, rec, _ = _record_september(x)
    _real(x)
    x.server.state.update(latest="2026-10", **({"layout": "cards"} if layout == "cards" else {}))
    t0 = time.time()
    run = _run_guided(x, page, rec, tab, period="2026-11")
    hits = _gen_hits(x)
    ok = run.get("result", {}).get("verdict") == "MISSING" and hits == []
    _report("not_posted_" + layout, ok, run, t0, {"downloaded": hits})
    _end(x, run)


def test_real_decision_redesigned_portal_picks_october(x):
    page, tab, rec, _ = _record_september(x)
    x.call("save_recording", id=rec["id"], goal="Download the September 2026 supplier invoice")
    _real(x)
    x.server.state.update(latest="2026-10", layout="cards")
    t0 = time.time()
    run = _run_guided(x, page, rec, tab)
    hits = _gen_hits(x)
    ok = run.get("result", {}).get("verdict") == "VERIFIED" and hits == ["/files/gen/invoice-2026-10.pdf"]
    _report("redesign_pick_october", ok, run, t0, {"downloaded": hits})
    _end(x, run)


def test_real_decision_harmless_survey_popup_closed(x):
    page, tab, rec = _record_payouts(x)
    _real(x)
    x.server.overrides["/portal/payouts.html"] = "portal/payouts_survey.html"
    t0 = time.time()
    run = _run_p0(x, page, tab, rec)
    state = page.evaluate("[window.__closed, window.__started]")
    ok = run.get("result", {}).get("verdict") == "VERIFIED" and state == [1, None]
    _report("survey_popup", ok, run, t0, {"closed_started": state})
    _end(x, run)


def test_real_decision_terms_popup_never_clicked(x):
    page, tab, rec = _record_payouts(x)
    _real(x)
    x.server.overrides["/portal/payouts.html"] = "portal/payouts_terms.html"
    t0 = time.time()
    run = _run_p0(x, page, tab, rec)
    agreed = page.evaluate("window.__agreed")
    ok = run.get("status") == "needs_user" and agreed is None
    _report("terms_popup", ok, run, t0, {"agreed": agreed})
    _end(x, run)
