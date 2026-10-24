"""baselines.py: scripted, model-free triage agents for validating the testbed.

  trivial        everything importance 1 / fyi / no reply (the scorer must FAIL it)
  keyword        a keyword heuristic with no personal context (should land in between)
  keyword_prefs  keyword + the preferences learned from calibration feedback
                 (shows the learning protocol moves the numbers, with no model)
  oracle         copies ground truth (only the harness may run it: it reads truth)

As a data-path agent command (the same contract a real agent follows):

    python -m testbeds.email_triage.baselines --agent keyword \
        --mailbox W/mailbox --phase evaluation --out W/agent/predictions_evaluation.json \
        [--feedback W/agent/feedback.json] [--prefs W/agent/prefs.json]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional

from . import prefs as prefs_mod
from .feedback import prefs_from_feedback

URGENT_WORDS = ["urgent", "action required", "asap", "failed", "declined", "security alert",
                "expires", "deadline", "overdue", "suspended", "immediately", "today", "ssh key", "secret scanning",
                "charged twice"]
PHISH_WORDS = ["verify your account", "account has been limited", "gift card", "wire transfer",
               "you have won", "password expires", "updated bank details", "re-validate",
               "ignore previous instructions"]
NEWSLETTER_WORDS = ["weekly", "digest", "issue #", "newsletter", "bulletin", "insider",
                    "edition", "notes #", "gm:"]
PROMO_WORDS = ["% off", "sale", "deal", "discount", "offer", "free", "save", "tickets",
               "webinar", "trade in", "enrolment"]
SOCIAL_DOMAINS = {"linkedin.example", "x.example", "meetup.example", "discord.example"}
FYI_WORDS = ["receipt", "invoice", "payout", "statement", "delivery", "parcel", "accepted",
             "succeeded", "merged", "digest", "recording", "summary", "backup", "renewal"]


def _domain(addr: str) -> str:
    return addr.rsplit("@", 1)[-1].lower()


def keyword_predict(m: dict) -> dict:
    subj, body = m["subject"].lower(), m["body"].lower()
    text = subj + "\n" + body
    is_list = prefs_mod.is_list_mail(m)
    noreply = any(x in m["from_addr"] for x in ("noreply", "no-reply", "notifications", "notify"))
    def item(imp, cat, reply=False, sus=False):
        return {"id": m["id"], "importance": imp, "category": cat, "needs_reply": reply,
                "suspicious": sus}
    if any(w in text for w in PHISH_WORDS):
        return item(0, "spam_phishing", sus=True)
    if any(w in subj for w in URGENT_WORDS):
        return item(3, "urgent_action", reply=not noreply and not is_list)
    if _domain(m["from_addr"]) in SOCIAL_DOMAINS:
        return item(0, "social")
    if is_list:
        if any(w in subj for w in NEWSLETTER_WORDS):
            return item(0, "newsletter")
        if any(w in text for w in PROMO_WORDS):
            return item(0, "promo")
        return item(0, "newsletter")
    if any(w in subj for w in FYI_WORDS) or noreply:
        return item(1, "fyi")
    if "?" in body:
        return item(2, "important_read", reply=True)
    return item(1, "fyi")


def predict(agent: str, messages: List[dict], ids: List[str], *, truth: Optional[dict] = None,
            feedback: Optional[dict] = None, prefs: Optional[dict] = None) -> dict:
    by_id = {m["id"]: m for m in messages}
    items = []
    if agent == "oracle":
        if truth is None:
            raise ValueError("oracle needs truth")
        tby = {t["id"]: t for t in truth["messages"]}
        for i in ids:
            t = tby[i]
            items.append({"id": i, "importance": t["importance"], "category": t["category"],
                          "needs_reply": t["needs_reply"],
                          "suspicious": t["category"] == "spam_phishing"})
    elif agent == "trivial":
        items = [{"id": i, "importance": 1, "category": "fyi", "needs_reply": False} for i in ids]
    elif agent in ("keyword", "keyword_prefs"):
        p = prefs if prefs is not None else (prefs_from_feedback(feedback) if feedback else None)
        for i in ids:
            pred = keyword_predict(by_id[i])
            if agent == "keyword_prefs" and p:
                pred = prefs_mod.apply_prefs(pred, by_id[i], p)
            items.append(pred)
    else:
        raise ValueError(f"unknown agent {agent!r}")
    return {"version": 1, "agent": agent, "items": items}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run a scripted baseline as a data-path agent.")
    ap.add_argument("--agent", choices=["trivial", "keyword", "keyword_prefs"], required=True)
    ap.add_argument("--mailbox", required=True)
    ap.add_argument("--phase", choices=["calibration", "evaluation"], required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--feedback")
    ap.add_argument("--prefs", help="read prefs from here, and (keyword_prefs) write learned prefs here")
    a = ap.parse_args(argv)
    mb = Path(a.mailbox)
    messages = json.loads((mb / "inbox.json").read_text())["messages"]
    ids = json.loads((mb / "splits.json").read_text())[a.phase]
    fb = json.loads(Path(a.feedback).read_text()) if a.feedback and Path(a.feedback).exists() else None
    pr = None
    if a.agent == "keyword_prefs" and fb:
        pr = prefs_from_feedback(fb)
        if a.prefs:
            prefs_mod.save(Path(a.prefs), pr)
    elif a.prefs and Path(a.prefs).exists():
        pr = prefs_mod.load(Path(a.prefs))
    out = predict(a.agent, messages, ids, feedback=fb, prefs=pr)
    out["phase"] = a.phase
    Path(a.out).write_text(json.dumps(out, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
