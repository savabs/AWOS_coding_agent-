"""correction_capture.py: implicit corrections from inbox state diffs.

The user corrects a triage agent mostly by DOING things, not typing: moving a
message to another label, removing a label, starring, archiving, moving it back
to the inbox, reporting phishing. This module compares two inbox snapshots,
subtracts every change AWOS itself made (replayed from the AWOS action log it is
given), and turns what is left (the user's own changes) into corrections in the
feedback format the learner already consumes (``testbeds/email_triage/feedback``:
items with id/from/subject/you_said/correct/feedback), plus a scoped rule
proposal per correction for the v2 preference store
(``testbeds/email_triage/prefs.record_evidence``).

Snapshot format (normalised; the testbed web inbox's ``GET /api/state`` already
has this shape, a Gmail/IMAP adapter only has to produce the same dict):

    {"messages": {"<id>": {"starred": bool, "archived": bool, "read": bool,
                           "priority": 0-3 | None, "category": str | None,
                           "needs_reply": bool, "reported_phishing": bool,
                           "labels": [str, ...]   # optional, generic provider labels
                          }, ...},
     "log_seq": int}      # optional: action-log length when the snapshot was taken

Action log format: one dict per AWOS action, as the testbed writes
``logs/actions.jsonl`` (seq, actor, action, email_id, params, status). Only
records whose actor is in ``awos_actors`` and whose status is 2xx are replayed.
Gmail/IMAP adapters should log AWOS's own modify calls in the same shape
(action = op name, params = op params, actor = "awos").

Stdlib only. Network: ``snapshot_from_webapp`` talks to 127.0.0.1 only.
"""
from __future__ import annotations

import copy
import json
import re
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

FIELDS = ("starred", "archived", "priority", "category", "needs_reply", "reported_phishing")
DEFAULT_STATE = {"read": False, "starred": False, "archived": False, "priority": None,
                 "category": None, "needs_reply": False, "reported_phishing": False}
AWOS_ACTORS = frozenset({"agent", "awos"})
JUNK = {"newsletter", "promo", "social"}
CAT_HUMAN = {
    "urgent_action": "Urgent", "important_read": "Important", "fyi": "FYI",
    "newsletter": "Newsletter", "promo": "Promotions", "social": "Social",
    "spam_phishing": "Spam / phishing",
}
# Sender local parts that mark machine mail ("services"): keyed by sender + subject.
SERVICE_LOCALPARTS = re.compile(
    r"^(no-?reply|do-?not-?reply|notifications?|news(letter)?s?|digest|updates?|info|hello|"
    r"team|marketing|offers|deals|weekly|daily|alerts?|mailer|bounce|support|billing|events)\b")
LOOPBACK = ("127.0.0.1", "localhost", "[::1]")


# --------------------------------------------------------------------------- snapshots

def normalise(snapshot: dict) -> Dict[str, dict]:
    """{"messages": {id: state}} -> {id: full state with defaults}."""
    msgs = snapshot.get("messages", snapshot) if isinstance(snapshot, dict) else {}
    out = {}
    for mid, st in (msgs or {}).items():
        s = dict(DEFAULT_STATE)
        s.update({k: v for k, v in (st or {}).items()})
        if "labels" in s:
            s["labels"] = sorted(set(s["labels"] or []))
        out[mid] = s
    return out


def snapshot_from_webapp(url: str, actions_log: Optional[Path] = None, timeout: float = 5.0) -> dict:
    """Fetch ``/api/state`` from the testbed web inbox (loopback only) and stamp it
    with the current action-log length so later diffs know which AWOS actions
    happened after it."""
    host = url.split("//", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0]
    if host not in LOOPBACK:
        raise ValueError(f"refusing non-loopback url {url!r}")
    with urllib.request.urlopen(url.rstrip("/") + "/api/state", timeout=timeout) as resp:
        snap = json.loads(resp.read().decode())
    if actions_log is not None:
        snap["log_seq"] = len(read_action_log(actions_log))
    return snap


def read_action_log(path: Path) -> List[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


# --------------------------------------------------------------------------- replay

def apply_op(state: dict, op: str, params: Optional[dict] = None) -> None:
    """Pure mirror of the web inbox's state ops (plus generic label ops) on one
    message state. Unknown ops are ignored."""
    params = params or {}
    if op == "star":
        state["starred"] = True
    elif op == "unstar":
        state["starred"] = False
    elif op == "archive":
        state["archived"] = True
    elif op in ("unarchive", "move_to_inbox"):
        state["archived"] = False
    elif op == "mark_read":
        state["read"] = True
    elif op == "mark_unread":
        state["read"] = False
    elif op == "open":
        state["read"] = True
    elif op == "mark_important":
        state["priority"] = max(state.get("priority") or 0, 2)
    elif op == "set_priority":
        try:
            state["priority"] = int(params.get("level"))
        except (TypeError, ValueError):
            pass
    elif op == "set_label":
        if params.get("category"):
            state["category"] = params["category"]
    elif op == "set_needs_reply":
        v = params.get("value", True)
        state["needs_reply"] = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "on", "yes")
    elif op == "report_phishing":
        state["reported_phishing"] = True
        state["category"] = "spam_phishing"
    elif op in ("add_label", "remove_label", "modify_labels"):
        labels = set(state.get("labels") or [])
        add = params.get("add") or ([params["label"]] if op == "add_label" and params.get("label") else [])
        rem = params.get("remove") or ([params["label"]] if op == "remove_label" and params.get("label") else [])
        labels |= set(add)
        labels -= set(rem)
        state["labels"] = sorted(labels)


def awos_records(log: Iterable[dict], since_seq: int = 0, until_seq: Optional[int] = None,
                 awos_actors: Iterable[str] = AWOS_ACTORS) -> List[dict]:
    actors = set(awos_actors)
    out = []
    for r in log:
        seq = int(r.get("seq") or 0)
        if seq <= since_seq or (until_seq is not None and seq > until_seq):
            continue
        if r.get("actor") not in actors or not r.get("email_id"):
            continue
        if not 200 <= int(r.get("status", 200)) < 300:
            continue
        out.append(r)
    return sorted(out, key=lambda r: int(r.get("seq") or 0))


def expected_state(before: dict, log: Iterable[dict], since_seq: int = 0,
                   until_seq: Optional[int] = None,
                   awos_actors: Iterable[str] = AWOS_ACTORS) -> Dict[str, dict]:
    """``before`` with AWOS's own successful actions replayed on top."""
    exp = copy.deepcopy(normalise(before))
    for r in awos_records(log, since_seq, until_seq, awos_actors):
        mid = r["email_id"]
        exp.setdefault(mid, dict(DEFAULT_STATE))
        apply_op(exp[mid], r.get("action", ""), r.get("params") or {})
    return exp


# --------------------------------------------------------------------------- diff

def user_changes(before: dict, after: dict, log: Iterable[dict] = (),
                 awos_actors: Iterable[str] = AWOS_ACTORS) -> List[dict]:
    """Field-level changes the USER made between ``before`` and ``after``.

    A field counts as a user change when ``after`` differs from what AWOS's own
    actions would have produced from ``before``. Read/unread is ignored (opening a
    message is not a judgement). Rows: {"id", "field", "before", "after", "awos"}
    where ``before`` is the AWOS-expected value and ``awos`` says whether AWOS had
    touched that field in this window."""
    log = list(log)
    since = int(before.get("log_seq") or 0) if isinstance(before, dict) else 0
    until = after.get("log_seq") if isinstance(after, dict) else None
    exp = expected_state(before, log, since, until, awos_actors)
    aft = normalise(after)
    touched: Dict[str, Set[str]] = {}
    for r in awos_records(log, since, until, awos_actors):
        touched.setdefault(r["email_id"], set()).add(_op_field(r))
    rows = []
    for mid in sorted(aft):
        e, a = exp.get(mid, dict(DEFAULT_STATE)), aft[mid]
        for f in FIELDS:
            if e.get(f) != a.get(f):
                rows.append({"id": mid, "field": f, "before": e.get(f), "after": a.get(f),
                             "awos": f in touched.get(mid, set())})
        if "labels" in a or "labels" in e:
            le, la = set(e.get("labels") or []), set(a.get("labels") or [])
            for lab in sorted(la - le):
                rows.append({"id": mid, "field": "label_added", "before": None, "after": lab,
                             "awos": False})
            for lab in sorted(le - la):
                rows.append({"id": mid, "field": "label_removed", "before": lab, "after": None,
                             "awos": "labels" in touched.get(mid, set())})
    return rows


def _op_field(r: dict) -> str:
    return {"set_priority": "priority", "mark_important": "priority", "set_label": "category",
            "set_needs_reply": "needs_reply", "star": "starred", "unstar": "starred",
            "archive": "archived", "unarchive": "archived", "report_phishing": "category",
            "add_label": "labels", "remove_label": "labels", "modify_labels": "labels"
            }.get(r.get("action", ""), "")


# --------------------------------------------------------------------------- scoping

def subject_stem(subject: str) -> str:
    """A stable subject pattern for a service: drop Re:/Fwd:, numbers and issue
    markers, keep the part before ':' / '|' / ' - ' when it has >= 2 words,
    else the first 3 words."""
    s = re.sub(r"^\s*((re|fwd?|aw)\s*:\s*)+", "", subject or "", flags=re.I).lower()
    s = re.sub(r"[#№]?\d[\d,./:-]*", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" -:|")
    for sep in (":", "|", " - ", " — "):
        if sep in s:
            head = s.split(sep, 1)[0].strip()
            if len(head.split()) >= 2:
                return head
    return " ".join(s.split()[:3])


def is_service_sender(msg: dict) -> bool:
    h = {k.lower() for k in (msg.get("headers") or {})}
    if "list-unsubscribe" in h or "list-id" in h:
        return True
    local = (msg.get("from_addr") or "").split("@", 1)[0].lower()
    return bool(SERVICE_LOCALPARTS.match(local))


def scope_for(msg: dict) -> tuple:
    """(scope, when) for a correction on ``msg``: people are keyed by address,
    services (list or machine senders) by address + subject pattern. Never domain:
    a domain-wide rule only comes from an explicit user choice."""
    addr = (msg.get("from_addr") or "").lower()
    if not addr:
        return "thread", {"thread_id": msg.get("thread_id", "")}
    if is_service_sender(msg):
        stem = subject_stem(msg.get("subject", ""))
        if stem:
            return "service", {"from_address": addr, "subject_any": [stem]}
    return "address", {"from_address": addr}


# --------------------------------------------------------------------------- corrections

def _pred(state: dict) -> dict:
    return {"importance": state.get("priority") if state.get("priority") is not None else 1,
            "category": state.get("category") or "fyi",
            "needs_reply": bool(state.get("needs_reply"))}


def corrections_from_changes(changes: List[dict], expected: Dict[str, dict],
                             after: Dict[str, dict], messages: Dict[str, dict]) -> List[dict]:
    """Group user changes per message into feedback items + rule proposals."""
    by_msg: Dict[str, List[dict]] = {}
    for c in changes:
        by_msg.setdefault(c["id"], []).append(c)
    items = []
    for mid, rows in by_msg.items():
        m = messages.get(mid, {"id": mid})
        e, a = expected.get(mid, dict(DEFAULT_STATE)), after.get(mid, dict(DEFAULT_STATE))
        you_said, correct = _pred(e), _pred(a)
        set_: Dict[str, Any] = {}
        exclude: Dict[str, Any] = {}
        phrases, strength = [], "strong"
        fields = {r["field"]: r for r in rows}
        if "category" in fields and a.get("category"):
            set_["category"] = a["category"]
            phrases.append(f"moved it to {CAT_HUMAN.get(a['category'], a['category'])}"
                           + (f" (I had {CAT_HUMAN.get(e['category'], e['category'])})"
                              if e.get("category") else ""))
            if a["category"] in JUNK and "priority" not in fields:
                set_["max_importance"] = 1 if a["category"] == "newsletter" else 0
            if a["category"] == "spam_phishing":
                set_.update({"importance": 0, "suspicious": True})
        elif "category" in fields and e.get("category"):
            exclude["category"] = e["category"]
            phrases.append(f"removed the {CAT_HUMAN.get(e['category'], e['category'])} label")
        if "priority" in fields and a.get("priority") is not None:
            set_["importance"] = a["priority"]
            set_.pop("max_importance", None)
            phrases.append(f"set priority {a['priority']}"
                           + (f" (I had {e['priority']})" if e.get("priority") is not None else ""))
        if "needs_reply" in fields:
            set_["needs_reply"] = bool(a.get("needs_reply"))
            phrases.append("marked it as needing a reply" if a.get("needs_reply")
                           else "said it needs no reply")
        if "reported_phishing" in fields and a.get("reported_phishing"):
            set_.update({"category": "spam_phishing", "importance": 0, "suspicious": True})
            phrases.append("reported it as phishing")
        if "starred" in fields:
            if a.get("starred"):
                if "importance" not in set_:
                    set_["min_importance"] = 2
                phrases.append("starred it")
            else:
                strength = "weak" if not set_ else strength
                phrases.append("removed the star")
        if "archived" in fields:
            if a.get("archived") is False:          # moved back to the inbox: strong
                if "importance" not in set_:
                    set_["min_importance"] = max(2, set_.get("min_importance", 0))
                if (e.get("category") or "") in JUNK | {"spam_phishing"} and "category" not in set_:
                    exclude["category"] = e["category"]
                phrases.append("moved it back to the inbox")
            else:                                  # archive: read-and-done is normal
                if not set_ and not exclude:
                    strength = "weak"
                phrases.append("archived it")
        for r in rows:
            if r["field"] == "label_removed":
                exclude.setdefault("labels_removed", []).append(r["before"])
                phrases.append(f"removed label {r['before']!r}")
            elif r["field"] == "label_added":
                phrases.append(f"added label {r['after']!r}")
        if "importance" in set_:
            correct["importance"] = set_["importance"]
        elif "min_importance" in set_:
            correct["importance"] = max(you_said["importance"], set_["min_importance"])
        elif "max_importance" in set_:
            correct["importance"] = min(you_said["importance"], set_["max_importance"])
        if "category" in set_:
            correct["category"] = set_["category"]
        scope, when = scope_for(m)
        proposals = []
        if set_:
            proposals.append({"when": when, "set": set_, "scope": scope, "effect": "include"})
        excl = {k: v for k, v in exclude.items() if k in ("category",)}
        if excl:
            proposals.append({"when": when, "set": excl, "scope": scope, "effect": "exclude"})
        frm = m.get("from") or m.get("from_addr", "?")
        items.append({
            "id": mid, "from": frm, "subject": m.get("subject", ""),
            "thread_id": m.get("thread_id", ""),
            "you_said": you_said, "correct": correct,
            "feedback": "The user " + "; ".join(phrases) + ".",
            "signal": {"changes": rows, "strength": strength},
            "scope": scope, "proposals": proposals,
        })
    return items


def capture(before: dict, after: dict, awos_log: Iterable[dict], messages: Iterable[dict],
            awos_actors: Iterable[str] = AWOS_ACTORS, include_weak: bool = False) -> dict:
    """Snapshots + AWOS's own action log -> a feedback document.

    ``messages`` are the message dicts (id, from, from_addr, from_name, subject,
    thread_id, headers). Weak signals (archive, unstar) are dropped unless
    ``include_weak``. The result has the same top-level shape as
    ``testbeds/email_triage/feedback.simulate_feedback`` output (version, phase,
    style, items) with phase/style "implicit"."""
    log = list(awos_log)
    by_id = {m["id"]: m for m in messages}
    since = int(before.get("log_seq") or 0)
    until = after.get("log_seq")
    exp = expected_state(before, log, since, until, awos_actors)
    changes = user_changes(before, after, log, awos_actors)
    items = corrections_from_changes(changes, exp, normalise(after), by_id)
    if not include_weak:
        items = [it for it in items if it["signal"]["strength"] != "weak"]
    ignored = sum(1 for r in awos_records(log, since, until, awos_actors) if _op_field(r))
    return {"version": 1, "phase": "implicit", "style": "implicit", "items": items,
            "awos_actions_ignored": ignored}


def capture_from_webapp_files(work: Path, before: dict, after: dict,
                              awos_actors: Iterable[str] = AWOS_ACTORS) -> dict:
    """Convenience for the testbed: messages from <work>/mailbox/inbox.json, the
    AWOS log from <work>/logs/actions.jsonl."""
    work = Path(work)
    inbox = json.loads((work / "mailbox" / "inbox.json").read_text())
    log = read_action_log(work / "logs" / "actions.jsonl")
    return capture(before, after, log, inbox["messages"], awos_actors)
