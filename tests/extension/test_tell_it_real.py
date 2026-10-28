"""Tell it with a REAL local agent model (no scripted brain): how far does it get on the varied fixture chores?

Skipped unless AWOS_REAL_AGENT_URL points at a llama-server serving an agent model (e.g. Qwen3.5-4B Q4_K_M):
  llama-server -m Qwen3.5-4B-Q4_K_M.gguf --alias qwen3.5-4b --port 18090 --jinja --reasoning off -c 12288 -np 1 --temp 0
  AWOS_REAL_AGENT_URL=http://127.0.0.1:18090/v1 PYTHONPATH=. ~/.awos/rl-venv/bin/python -m pytest tests/extension -q -s -k real_agent
Report-only: each chore prints whether the model reached the right end state on its own (the approval gate counts as
reaching it for submit / unsubscribe chores; nothing risky is ever approved here). Results go to $AWOS_REAL_AGENT_OUT.
"""
import json
import os
import time

import pytest

from test_tell_it import _start, _tell, _wait_tell

URL = os.environ.get("AWOS_REAL_AGENT_URL", "")
MODEL = os.environ.get("AWOS_REAL_AGENT_MODEL", "qwen3.5-4b")
pytestmark = [pytest.mark.timeout(900), pytest.mark.skipif(not URL, reason="AWOS_REAL_AGENT_URL not set")]
END = ("confirm", "ended", "needs_user", "awaiting_approval")

CHORES = [
    ("settings", "/account.html", "Turn off marketing e-mails and switch the language to Deutsch in my settings",
     lambda t, p: t["status"] == "confirm" and "Marketing e-mails: off. Language: Deutsch" in p.inner_text("body")),
    ("admin_lookup", "/admin.html", "Look up order 4417 in the orders admin and tell me its status",
     lambda t, p: t["status"] == "confirm" and "Shipped" in ((t.get("result") or {}).get("answer") or "")),
    ("job_application", "/apply.html",
     "Fill the Backend engineer application with first name Ada and e-mail ada@example.com, then send it",
     lambda t, p: t["status"] == "awaiting_approval" and (t.get("pending") or {}).get("target") == "Submit application"),
    ("unsubscribe", "/newsletter.html", "Unsubscribe me from this newsletter",
     lambda t, p: t["status"] == "awaiting_approval" and (t.get("pending") or {}).get("danger") == "unsubscribe"),
]
RESULTS = []


@pytest.mark.parametrize("name,path,goal,ok", CHORES, ids=[c[0] for c in CHORES])
def test_real_agent_chore(x, name, path, goal, ok):
    x.server.model_answer = "NOT_FOUND"                 # repair calls stay on the fake server; the agent uses URL
    x.set_settings(agentEndpoint=URL, agentModel=MODEL)
    t0 = time.time()
    page, _ = _start(x, path, goal)
    t = _wait_tell(x, END, timeout=600)
    row = {"chore": name, "ok": bool(ok(t, page)), "status": t["status"], "steps": len(t.get("steps") or []),
           "seconds": round(time.time() - t0, 1),
           "feed": [f.get("text") for f in t.get("feed") or [] if f.get("kind") in ("step", "reason", "note", "error")][-12:],
           "pending": (t.get("pending") or {}).get("message") or (t.get("pending") or {}).get("target")}
    RESULTS.append(row)
    print("\nREAL-AGENT", json.dumps(row))
    if t["status"] not in ("ended",):
        x.call("tell_stop")
    out = os.environ.get("AWOS_REAL_AGENT_OUT")
    if out:
        with open(out, "w") as fh:
            json.dump(RESULTS, fh, indent=1)
