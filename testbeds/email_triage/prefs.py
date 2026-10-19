"""prefs.py: the preferences store the triage agent writes and reads.

Stdlib only. The file is JSON, normally ``<work>/agent/prefs.json``:

    {
      "version": 1,
      "notes": ["Acme is my biggest client", ...],      # free text the agent keeps
      "rules": [
        {
          "id": "client_acme_1",                  # unique within the file
          "when": {                               # every key given must match (AND)
            "from_address":        "x@y.example", # exact, case-insensitive
            "from_domain":         "acme.example",# exact domain of the sender
            "from_domain_any":     ["a.example"], # any of
            "from_domain_not_any": ["b.example"], # none of
            "from_name_any":       ["acme"],      # substring of the display name
            "subject_any":         ["urgent"],    # substring of the subject
            "body_any":            ["?"],         # substring of the body
            "text_any":            ["invoice"],   # substring of subject + body
            "is_list":             true           # has a List-Unsubscribe / List-Id header
          },
          "set": {                                # what the rule decides
            "importance": 0-3, "min_importance": 0-3, "max_importance": 0-3,
            "category": "<one of CATEGORIES>",
            "needs_reply": true|false,
            "suspicious": true|false
          },
          "source": "correction" | "user" | "agent",
          "note": "free text: why"
        }
      ]
    }

Rules are applied in file order; a later rule overrides an earlier one field
by field. ``apply_prefs`` is a reference implementation an agent may use, or
the agent may keep its own representation, as long as the file it writes
validates. The scorer never reads this file; it only scores predictions.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

CATEGORIES = ["urgent_action", "important_read", "fyi", "newsletter", "promo",
              "social", "spam_phishing"]

WHEN_KEYS = {
    "from_address": str, "from_domain": str, "from_domain_any": list,
    "from_domain_not_any": list, "from_name_any": list, "subject_any": list,
    "body_any": list, "text_any": list, "is_list": bool,
}
SET_KEYS = {"importance": int, "min_importance": int, "max_importance": int,
            "category": str, "needs_reply": bool, "suspicious": bool}
SOURCES = {"correction", "user", "agent"}


def empty_prefs() -> dict:
    return {"version": 1, "notes": [], "rules": []}


def validate(prefs: Any) -> List[str]:
    """Return a list of problems; empty means valid."""
    errs: List[str] = []
    if not isinstance(prefs, dict):
        return ["prefs must be a JSON object"]
    if prefs.get("version") != 1:
        errs.append("version must be 1")
    if not isinstance(prefs.get("notes", []), list):
        errs.append("notes must be a list")
    rules = prefs.get("rules", [])
    if not isinstance(rules, list):
        return errs + ["rules must be a list"]
    seen = set()
    for i, r in enumerate(rules):
        where = f"rules[{i}]"
        if not isinstance(r, dict):
            errs.append(f"{where} must be an object")
            continue
        rid = r.get("id")
        if not isinstance(rid, str) or not rid:
            errs.append(f"{where}.id missing")
        elif rid in seen:
            errs.append(f"{where}.id duplicate {rid!r}")
        seen.add(rid)
        when, st = r.get("when"), r.get("set")
        if not isinstance(when, dict) or not when:
            errs.append(f"{where}.when must be a non-empty object")
        else:
            for k, v in when.items():
                if k not in WHEN_KEYS:
                    errs.append(f"{where}.when.{k} unknown")
                elif not isinstance(v, WHEN_KEYS[k]):
                    errs.append(f"{where}.when.{k} must be {WHEN_KEYS[k].__name__}")
        if not isinstance(st, dict) or not st:
            errs.append(f"{where}.set must be a non-empty object")
        else:
            for k, v in st.items():
                if k not in SET_KEYS:
                    errs.append(f"{where}.set.{k} unknown")
                elif not isinstance(v, SET_KEYS[k]) or (SET_KEYS[k] is int and isinstance(v, bool)):
                    errs.append(f"{where}.set.{k} must be {SET_KEYS[k].__name__}")
                elif SET_KEYS[k] is int and not 0 <= v <= 3:
                    errs.append(f"{where}.set.{k} must be 0-3")
                elif k == "category" and v not in CATEGORIES:
                    errs.append(f"{where}.set.category {v!r} unknown")
        if "source" in r and r["source"] not in SOURCES:
            errs.append(f"{where}.source must be one of {sorted(SOURCES)}")
    return errs


def load(path: Path) -> dict:
    path = Path(path)
    if not path.exists():
        return empty_prefs()
    data = json.loads(path.read_text())
    errs = validate(data)
    if errs:
        raise ValueError("invalid prefs: " + "; ".join(errs))
    return data


def save(path: Path, prefs: dict) -> None:
    errs = validate(prefs)
    if errs:
        raise ValueError("invalid prefs: " + "; ".join(errs))
    Path(path).write_text(json.dumps(prefs, indent=2) + "\n")


def _domain(addr: str) -> str:
    return addr.rsplit("@", 1)[-1].lower() if "@" in addr else ""


def is_list_mail(msg: dict) -> bool:
    h = {k.lower() for k in (msg.get("headers") or {})}
    return "list-unsubscribe" in h or "list-id" in h


def matches(when: dict, msg: dict) -> bool:
    addr = (msg.get("from_addr") or "").lower()
    dom = _domain(addr)
    name = (msg.get("from_name") or "").lower()
    subj = (msg.get("subject") or "").lower()
    body = (msg.get("body") or "").lower()

    def any_in(hay: str, needles: list) -> bool:
        return any(str(n).lower() in hay for n in needles)

    for k, v in when.items():
        if k == "from_address" and addr != v.lower():
            return False
        if k == "from_domain" and dom != v.lower():
            return False
        if k == "from_domain_any" and dom not in {d.lower() for d in v}:
            return False
        if k == "from_domain_not_any" and dom in {d.lower() for d in v}:
            return False
        if k == "from_name_any" and not any_in(name, v):
            return False
        if k == "subject_any" and not any_in(subj, v):
            return False
        if k == "body_any" and not any_in(body, v):
            return False
        if k == "text_any" and not any_in(subj + "\n" + body, v):
            return False
        if k == "is_list" and is_list_mail(msg) != v:
            return False
    return True


def apply_prefs(pred: dict, msg: dict, prefs: dict) -> dict:
    """Return a copy of ``pred`` with every matching rule applied in order."""
    out = dict(pred)
    for r in prefs.get("rules", []):
        if not matches(r["when"], msg):
            continue
        st = r["set"]
        if "importance" in st:
            out["importance"] = st["importance"]
        if "min_importance" in st:
            out["importance"] = max(out.get("importance", 1), st["min_importance"])
        if "max_importance" in st:
            out["importance"] = min(out.get("importance", 1), st["max_importance"])
        for k in ("category", "needs_reply", "suspicious"):
            if k in st:
                out[k] = st[k]
    return out
