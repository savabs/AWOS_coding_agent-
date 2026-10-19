"""prefs.py: the preferences store the triage agent writes and reads.

Stdlib only. The file is JSON, normally ``<work>/agent/prefs.json``. Two
schema versions load; v1 files written before WP2 keep working unchanged.

Version 1 (still accepted, still the default output of ``prefs_from_feedback``):

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
            "is_list":             true,          # has a List-Unsubscribe / List-Id header
            "thread_id":           "t1a2b3c"      # (v2) exactly this thread
          },
          "set": {                                # what the rule decides
            "importance": 0-3, "min_importance": 0-3, "max_importance": 0-3,
            "category": "<one of CATEGORIES>",
            "needs_reply": true|false,
            "suspicious": true|false
          },
          "source": "correction" | "user" | "agent" | "implicit",
          "note": "free text: why"
        }
      ]
    }

Version 2 adds optional per-rule fields (all optional; defaults in brackets):

    "source":   "user" | "correction" | "implicit" | "agent"      [correction]
                 user       = the user wrote/edited the rule (rules editor)
                 correction = derived from an explicit correction the user typed
                 implicit   = derived from what the user DID in the inbox
                              (label removed/added, moved, starred, archived)
                 agent      = the agent's own repeated decisions
    "scope":    "thread" | "service" | "subject" | "address" | "domain" | "custom"
                 [inferred from ``when``]. service = sender address + subject
                 pattern (SaneBox's key for machine mail: one company's newsletter
                 and its security alerts are different services).
    "effect":   "include" | "exclude"                              [include]
                 exclude = for matching mail the values in ``set`` are WRONG:
                 lower-or-equal precedence rules may not assign them, and if the
                 model assigned them they are moved off (see ``resolve``).
    "status":   "active" | "candidate" | "disabled"                [active]
                 candidates are never applied; they only collect evidence and
                 can be shown to the model as hints.
    "created", "updated", "last_hit": ISO-8601 strings
    "hits":     int, how many times the rule decided something
    "evidence": [{"thread_id", "message_id", "set", "model_agreed", "at"}]
    "contradictions": int (evidence that disagreed since the last reset)
    "promoted": ISO-8601 when a candidate became active
    "provenance": {"message_ids": [...], "feedback": "...", "signal": "..."}

Precedence (highest wins; ``precedence`` returns the sort key):

    user  >  explicit subject/service/thread  >  explicit address  >
    explicit domain  >  learned (implicit, then agent)  >  model

i.e. key = (source rank, scope rank, updated, file position). Rules are applied
lowest first so a higher one overrides field by field; within the same rank the
newest ``updated`` wins and, on a full tie, the later rule in the file. For v1
files (no timestamps, one source) this keeps file order except that narrower
scopes override broader ones.

Conflicts: an exclude beats an include of equal or lower precedence for the
values it names; an include of HIGHER precedence beats the exclude.

Safety guard (fail toward the cheap error): a demoting rule (importance or
max_importance <= 1, or category newsletter/promo/social) whose scope is domain
or custom and whose source is NOT ``user`` never applies to a non-list message
that looks like a security/account/payment alert. So "junk Nimbus's newsletter"
written too broadly cannot bury Nimbus's "your password was changed".

Promotion gate (implicit and agent sources): a pattern becomes active only after
evidence from >= 3 distinct threads, no contradicting evidence, AND agreement
from the model (an evidence row with ``model_agreed`` true, or ``model_check``
returning True in ``promote``). Until then it stays a ``candidate``.

``apply_prefs`` / ``resolve`` are the reference implementation. The scorer never
reads this file; it only scores predictions.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

CATEGORIES = ["urgent_action", "important_read", "fyi", "newsletter", "promo",
              "social", "spam_phishing"]

WHEN_KEYS = {
    "from_address": str, "from_domain": str, "from_domain_any": list,
    "from_domain_not_any": list, "from_name_any": list, "subject_any": list,
    "body_any": list, "text_any": list, "is_list": bool, "thread_id": str,
}
SET_KEYS = {"importance": int, "min_importance": int, "max_importance": int,
            "category": str, "needs_reply": bool, "suspicious": bool}
SOURCES = {"correction", "user", "agent", "implicit"}
SCOPES = {"thread", "service", "subject", "address", "domain", "custom"}
EFFECTS = {"include", "exclude"}
STATUSES = {"active", "candidate", "disabled"}
VERSIONS = {1, 2}

SOURCE_RANK = {"agent": 1, "implicit": 2, "correction": 3, "user": 4}
SCOPE_RANK = {"custom": 0, "domain": 1, "address": 2, "subject": 3, "service": 4, "thread": 5}
LEARNED_SOURCES = {"implicit", "agent"}       # need the promotion gate
PROMOTE_MIN_THREADS = 3
JUNK_CATEGORIES = {"newsletter", "promo", "social"}
DEFAULT_PRED = {"importance": 1, "category": "fyi", "needs_reply": False, "suspicious": False}

# Words that mark mail about the user's account, credentials or money. Used only by
# the safety guard, and only for non-list mail.
PROTECTED_TERMS = (
    "security alert", "password", "sign-in", "signin", "new login", "login attempt",
    "2fa", "two-factor", "verification code", "ssh key", "secret scanning",
    "account was", "account has been", "suspended", "payment failed", "payment was declined",
    "payout failed", "declined", "expires", "expired", "breach", "unauthorized",
    "unusual activity", "action required",
)


# --------------------------------------------------------------------------- basics

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def empty_prefs(version: int = 1) -> dict:
    return {"version": version, "notes": [], "rules": []}


def _domain(addr: str) -> str:
    return addr.rsplit("@", 1)[-1].lower() if "@" in addr else ""


def is_list_mail(msg: dict) -> bool:
    h = {k.lower() for k in (msg.get("headers") or {})}
    return "list-unsubscribe" in h or "list-id" in h


def infer_scope(when: dict) -> str:
    """The narrowest scope a ``when`` block expresses."""
    if "thread_id" in when:
        return "thread"
    if "from_address" in when and "subject_any" in when:
        return "service"
    if "from_address" in when:
        return "address"
    if "subject_any" in when and not ({"from_domain", "from_domain_any"} & set(when)):
        return "subject"
    if {"from_domain", "from_domain_any"} & set(when):
        return "domain"
    return "custom"


def rule_scope(r: dict) -> str:
    s = r.get("scope")
    return s if s in SCOPES else infer_scope(r.get("when") or {})


def rule_source(r: dict) -> str:
    s = r.get("source")
    return s if s in SOURCES else "correction"


def rule_status(r: dict) -> str:
    s = r.get("status")
    return s if s in STATUSES else "active"


def rule_effect(r: dict) -> str:
    e = r.get("effect")
    return e if e in EFFECTS else "include"


def precedence(r: dict, pos: int = 0) -> Tuple[int, int, str, int]:
    """Sort key: higher = wins. (source rank, scope rank, updated, file position)."""
    return (SOURCE_RANK[rule_source(r)], SCOPE_RANK[rule_scope(r)],
            str(r.get("updated") or r.get("created") or ""), pos)


# --------------------------------------------------------------------------- validation

def _validate_when_set(where: str, when: Any, st: Any, errs: List[str]) -> None:
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


def validate(prefs: Any) -> List[str]:
    """Return a list of problems; empty means valid. Accepts version 1 and 2."""
    errs: List[str] = []
    if not isinstance(prefs, dict):
        return ["prefs must be a JSON object"]
    version = prefs.get("version")
    if version not in VERSIONS:
        errs.append("version must be 1 or 2")
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
        _validate_when_set(where, r.get("when"), r.get("set"), errs)
        if "source" in r and r["source"] not in SOURCES:
            errs.append(f"{where}.source must be one of {sorted(SOURCES)}")
        for key, allowed in (("scope", SCOPES), ("effect", EFFECTS), ("status", STATUSES)):
            if key in r and r[key] not in allowed:
                errs.append(f"{where}.{key} must be one of {sorted(allowed)}")
        for key in ("created", "updated", "last_hit", "promoted"):
            if key in r and not isinstance(r[key], str):
                errs.append(f"{where}.{key} must be an ISO-8601 string")
        for key in ("hits", "contradictions"):
            if key in r and (not isinstance(r[key], int) or isinstance(r[key], bool) or r[key] < 0):
                errs.append(f"{where}.{key} must be a non-negative int")
        if "evidence" in r:
            if not isinstance(r["evidence"], list):
                errs.append(f"{where}.evidence must be a list")
            else:
                for j, e in enumerate(r["evidence"]):
                    if not isinstance(e, dict):
                        errs.append(f"{where}.evidence[{j}] must be an object")
        if "provenance" in r and not isinstance(r["provenance"], dict):
            errs.append(f"{where}.provenance must be an object")
        if version == 1 and any(k in r for k in ("scope", "effect", "status", "evidence")):
            errs.append(f"{where}: scope/effect/status/evidence need version 2")
    return errs


def load(path: Path, upgrade_to_v2: bool = False) -> dict:
    """Load a v1 or v2 file. With ``upgrade_to_v2`` a v1 file is returned as v2."""
    path = Path(path)
    if not path.exists():
        return empty_prefs(2 if upgrade_to_v2 else 1)
    data = json.loads(path.read_text())
    errs = validate(data)
    if errs:
        raise ValueError("invalid prefs: " + "; ".join(errs))
    return upgrade(data) if upgrade_to_v2 else data


def save(path: Path, prefs: dict) -> None:
    errs = validate(prefs)
    if errs:
        raise ValueError("invalid prefs: " + "; ".join(errs))
    Path(path).write_text(json.dumps(prefs, indent=2) + "\n")


def upgrade(prefs: dict, now: Optional[str] = None) -> dict:
    """Return a v2 copy of a v1 (or v2) document. Rule behaviour is unchanged:
    every v1 rule becomes an active include with its inferred scope."""
    out = copy.deepcopy(prefs)
    if out.get("version") == 2:
        return out
    out["version"] = 2
    for r in out.get("rules", []):
        r.setdefault("source", "correction")
        r.setdefault("scope", infer_scope(r.get("when") or {}))
        r.setdefault("effect", "include")
        r.setdefault("status", "active")
        r.setdefault("hits", 0)
        if now:
            r.setdefault("created", now)
            r.setdefault("updated", now)
    return out


# --------------------------------------------------------------------------- matching

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
        if k == "thread_id" and (msg.get("thread_id") or "") != v:
            return False
    return True


def is_protected(msg: dict) -> bool:
    """Non-list mail that reads like a security / account / payment alert."""
    if is_list_mail(msg):
        return False
    text = ((msg.get("subject") or "") + "\n" + (msg.get("body") or "")).lower()
    return any(t in text for t in PROTECTED_TERMS)


def is_demoting(st: dict) -> bool:
    if st.get("suspicious") or st.get("category") == "spam_phishing":
        return False          # flagging phishing protects the user; never guarded
    if "importance" in st and st["importance"] <= 1:
        return True
    if "max_importance" in st and st["max_importance"] <= 1:
        return True
    return st.get("category") in JUNK_CATEGORIES


def guard_blocks(r: dict, msg: dict) -> bool:
    """The safety guard: a broad, non-user demotion may not touch a protected message."""
    return (rule_source(r) != "user" and rule_scope(r) in ("domain", "custom")
            and is_demoting(r.get("set") or {}) and is_protected(msg))


# --------------------------------------------------------------------------- resolution

def _set_field(out: dict, k: str, v: Any) -> None:
    if k == "importance":
        out["importance"] = v
    elif k == "min_importance":
        out["importance"] = max(out.get("importance", 1), v)
    elif k == "max_importance":
        out["importance"] = min(out.get("importance", 1), v)
    else:
        out[k] = v


def _excluded(st: dict, exclusions: List[Tuple[tuple, dict]], key: tuple, k: str, v: Any) -> bool:
    """Is assigning field k=v (from a rule with precedence ``key``) blocked by an
    exclude of equal or higher precedence?"""
    for ekey, est in exclusions:
        if ekey[:3] < key[:3]:
            continue                       # a higher-ranked (or newer) include beats the exclude
        if k in ("importance", "min_importance", "max_importance"):
            if "importance" in est and est["importance"] == v:
                return True
        elif k in est and est[k] == v:
            return True
    return False


def _move_off(out: dict, est: dict, set_by_rule: set) -> List[str]:
    """Move a MODEL value off an excluded value (fields no include rule set).
    Returns changed field names."""
    changed = []
    est = {k: v for k, v in est.items() if k not in set_by_rule}
    if "category" in est and out.get("category") == est["category"]:
        out["category"] = "important_read" if est["category"] == "fyi" else "fyi"
        changed.append("category")
    if "importance" in est and out.get("importance") == est["importance"]:
        v = est["importance"]
        out["importance"] = v + 1 if v < 2 else v - 1
        changed.append("importance")
    for k in ("needs_reply", "suspicious"):
        if k in est and out.get(k) == est[k]:
            out[k] = not est[k]
            changed.append(k)
    return changed


def resolve(pred: dict, msg: dict, prefs: dict, record_hits: bool = False,
            now: Optional[str] = None) -> Tuple[dict, List[dict]]:
    """Apply every active matching rule by precedence. Returns (decision, trace).

    trace rows: {"rule": id, "action": "applied"|"excluded_fields"|"guarded"|"exclude",
    "fields": [...]}. With ``record_hits`` the rules that decided something get
    ``hits`` += 1 and ``last_hit`` = now (prefs is mutated)."""
    out = dict(pred)
    trace: List[dict] = []
    active = [(precedence(r, i), r) for i, r in enumerate(prefs.get("rules", []))
              if rule_status(r) == "active" and matches(r.get("when") or {}, msg)]
    active.sort(key=lambda t: t[0])
    exclusions = [(key, r.get("set") or {}) for key, r in active if rule_effect(r) == "exclude"]
    hit: List[dict] = []
    set_by_rule: set = set()
    for key, r in active:
        st = r.get("set") or {}
        if rule_effect(r) == "exclude":
            trace.append({"rule": r.get("id"), "action": "exclude", "fields": sorted(st)})
            continue
        if guard_blocks(r, msg):
            trace.append({"rule": r.get("id"), "action": "guarded", "fields": sorted(st)})
            continue
        applied, blocked = [], []
        for k, v in st.items():
            if exclusions and _excluded(st, exclusions, key, k, v):
                blocked.append(k)
                continue
            _set_field(out, k, v)
            applied.append(k)
            set_by_rule.add("importance" if k.endswith("importance") else k)
        if blocked:
            trace.append({"rule": r.get("id"), "action": "excluded_fields", "fields": blocked})
        if applied:
            trace.append({"rule": r.get("id"), "action": "applied", "fields": applied})
            hit.append(r)
    # An exclude also corrects the model (the lowest layer) when no include re-set it.
    for key, r in active:
        if rule_effect(r) != "exclude":
            continue
        changed = _move_off(out, r.get("set") or {}, set_by_rule)
        if changed:
            trace.append({"rule": r.get("id"), "action": "moved_off_model", "fields": changed})
            hit.append(r)
    if record_hits:
        ts = now or now_iso()
        for r in hit:
            r["hits"] = int(r.get("hits") or 0) + 1
            r["last_hit"] = ts
    return out, trace


def apply_prefs(pred: dict, msg: dict, prefs: dict) -> dict:
    """Return a copy of ``pred`` with every active matching rule applied by precedence."""
    return resolve(pred, msg, prefs)[0]


def explain(msg: dict, prefs: dict, pred: Optional[dict] = None) -> List[str]:
    """Human-readable lines: which rules decided this message and why."""
    out, trace = resolve(pred or dict(DEFAULT_PRED), msg, prefs)
    by_id = {r.get("id"): r for r in prefs.get("rules", [])}
    lines = []
    for t in trace:
        r = by_id.get(t["rule"], {})
        lines.append(f"{t['action']}: {describe_rule(r)} [{', '.join(t['fields'])}]")
    return lines


# --------------------------------------------------------------------------- conflicts

def _canon_when(when: dict) -> str:
    norm = {k: (sorted(str(x).lower() for x in v) if isinstance(v, list)
                else (v.lower() if isinstance(v, str) else v)) for k, v in when.items()}
    return json.dumps(norm, sort_keys=True)


def find_conflicts(prefs: dict) -> List[dict]:
    """Pairs of active rules with the same ``when`` and the same precedence rank that
    set the same field to different values (or an include and exclude of the same
    value). The newer one wins at resolve time; this lists them for the editor."""
    out = []
    rules = [r for r in prefs.get("rules", []) if rule_status(r) == "active"]
    for i, a in enumerate(rules):
        for b in rules[i + 1:]:
            if _canon_when(a["when"]) != _canon_when(b["when"]):
                continue
            if precedence(a)[:2] != precedence(b)[:2]:
                continue
            sa, sb = a.get("set") or {}, b.get("set") or {}
            fields = []
            if rule_effect(a) == rule_effect(b):
                fields = [k for k in sa if k in sb and sa[k] != sb[k]]
            else:
                fields = [k for k in sa if k in sb and sa[k] == sb[k]]
            if fields:
                winner = max((precedence(a, 0), a), (precedence(b, 1), b), key=lambda t: t[0])[1]
                out.append({"rules": [a["id"], b["id"]], "fields": fields, "winner": winner["id"]})
    return out


# --------------------------------------------------------------------------- learning

def _new_id(prefs: dict, stem: str) -> str:
    ids = {r.get("id") for r in prefs.get("rules", [])}
    base = re.sub(r"[^a-z0-9_]+", "_", stem.lower()).strip("_")[:40] or "rule"
    n = 1
    while f"{base}_{n}" in ids:
        n += 1
    return f"{base}_{n}"


def find_rule(prefs: dict, when: dict, effect: str = "include",
              source: Optional[str] = None) -> Optional[dict]:
    cw = _canon_when(when)
    for r in prefs.get("rules", []):
        if _canon_when(r.get("when") or {}) == cw and rule_effect(r) == effect \
                and (source is None or rule_source(r) == source):
            return r
    return None


def add_rule(prefs: dict, when: dict, set_: dict, *, source: str = "user",
             scope: Optional[str] = None, effect: str = "include", note: str = "",
             rule_id: Optional[str] = None, now: Optional[str] = None,
             provenance: Optional[dict] = None) -> dict:
    """Add or update an explicit (user / correction) rule; active immediately.
    Same ``when`` + effect + source: the existing rule is updated (newest wins)."""
    if prefs.get("version") != 2:
        prefs.update(upgrade(prefs, now))
    ts = now or now_iso()
    r = find_rule(prefs, when, effect, source)
    status = "candidate" if source in LEARNED_SOURCES else "active"
    if r is None:
        r = {"id": rule_id or _new_id(prefs, f"{source}_{scope or infer_scope(when)}"),
             "when": dict(when), "set": dict(set_), "source": source,
             "scope": scope or infer_scope(when), "effect": effect, "status": status,
             "created": ts, "updated": ts, "hits": 0}
        if note:
            r["note"] = note
        if provenance:
            r["provenance"] = dict(provenance)
        prefs.setdefault("rules", []).append(r)
    else:
        r["set"] = dict(set_)
        r["updated"] = ts
        if source not in LEARNED_SOURCES:
            r["status"] = "active"
        if note:
            r["note"] = note
        if provenance:
            r.setdefault("provenance", {}).update(provenance)
    return r


def record_evidence(prefs: dict, when: dict, set_: dict, *, thread_id: str,
                    message_id: str = "", source: str = "implicit",
                    scope: Optional[str] = None, effect: str = "include",
                    model_agreed: Optional[bool] = None, note: str = "",
                    now: Optional[str] = None, min_threads: int = PROMOTE_MIN_THREADS) -> dict:
    """Add one observation for a learned (implicit/agent) pattern.

    Creates a candidate if new. Evidence whose ``set`` differs from the rule's is a
    contradiction: an active learned rule falls back to candidate, and the newest
    value becomes the candidate's ``set`` with its evidence reset (newest wins).
    Explicit sources go straight to ``add_rule``. Returns the rule."""
    if source not in LEARNED_SOURCES:
        return add_rule(prefs, when, set_, source=source, scope=scope, effect=effect,
                        note=note, now=now, provenance={"message_ids": [message_id]})
    if prefs.get("version") != 2:
        prefs.update(upgrade(prefs, now))
    ts = now or now_iso()
    r = find_rule(prefs, when, effect, source)
    row = {"thread_id": thread_id, "message_id": message_id, "set": dict(set_), "at": ts}
    if model_agreed is not None:
        row["model_agreed"] = bool(model_agreed)
    if r is None:
        r = add_rule(prefs, when, set_, source=source, scope=scope, effect=effect,
                     note=note, now=ts, provenance={"message_ids": []})
        r["evidence"], r["contradictions"] = [], 0
    if r.get("set") != set_:
        r["contradictions"] = int(r.get("contradictions") or 0) + 1
        r["status"] = "candidate"
        r["set"] = dict(set_)
        r["evidence"] = []
        r.pop("promoted", None)
    r.setdefault("evidence", []).append(row)
    r["updated"] = ts
    prov = r.setdefault("provenance", {})
    if message_id:
        prov.setdefault("message_ids", []).append(message_id)
    _maybe_promote(r, None, min_threads, ts)
    return r


def _maybe_promote(r: dict, model_check: Optional[Callable[[dict], bool]],
                   min_threads: int, ts: str) -> bool:
    if rule_status(r) != "candidate" or rule_source(r) not in LEARNED_SOURCES:
        return False
    ev = [e for e in r.get("evidence", []) if e.get("set") == r.get("set")]
    threads = {e.get("thread_id") for e in ev if e.get("thread_id")}
    if len(threads) < min_threads:
        return False
    agreed = any(e.get("model_agreed") is True for e in ev)
    if not agreed and model_check is not None:
        agreed = bool(model_check(r))
    if not agreed:
        return False
    r["status"] = "active"
    r["promoted"] = ts
    return True


def promote(prefs: dict, model_check: Optional[Callable[[dict], bool]] = None,
            min_threads: int = PROMOTE_MIN_THREADS, now: Optional[str] = None) -> List[str]:
    """Run the promotion gate over every candidate. ``model_check(rule) -> bool``
    asks the model whether it agrees with the candidate (e.g. classify the newest
    evidence message with the candidate as a hint). Returns promoted rule ids."""
    ts = now or now_iso()
    return [r["id"] for r in prefs.get("rules", []) if _maybe_promote(r, model_check, min_threads, ts)]


def candidate_progress(r: dict, min_threads: int = PROMOTE_MIN_THREADS) -> dict:
    ev = [e for e in r.get("evidence", []) if e.get("set") == r.get("set")]
    threads = {e.get("thread_id") for e in ev if e.get("thread_id")}
    return {"threads": len(threads), "needed": min_threads,
            "model_agreed": any(e.get("model_agreed") is True for e in ev),
            "contradictions": int(r.get("contradictions") or 0)}


def hints_for(msg: dict, prefs: dict, k: int = 10) -> List[str]:
    """Up to ``k`` short lines about this sender for the model's USER turn (keeps the
    system prefix cacheable): active rules and candidates whose sender part matches."""
    addr = (msg.get("from_addr") or "").lower()
    dom = _domain(addr)
    rows = []
    for i, r in enumerate(prefs.get("rules", [])):
        if rule_status(r) == "disabled":
            continue
        w = r.get("when") or {}
        sender_hit = (w.get("from_address", "").lower() == addr
                      or w.get("from_domain", "").lower() == dom
                      or dom in {d.lower() for d in w.get("from_domain_any", [])}
                      or (w.get("thread_id") and w.get("thread_id") == msg.get("thread_id")))
        if not sender_hit:
            continue
        rows.append((precedence(r, i), r))
    rows.sort(key=lambda t: t[0], reverse=True)
    out = []
    for _, r in rows[:k]:
        tag = "candidate, unconfirmed" if rule_status(r) == "candidate" else rule_source(r)
        out.append(f"{describe_rule(r)} ({tag})")
    return out


# --------------------------------------------------------------------------- render

_IMP = {0: "not important (junk)", 1: "low", 2: "important", 3: "urgent"}
_CAT = {"urgent_action": "Urgent", "important_read": "Important", "fyi": "FYI",
        "newsletter": "Newsletter", "promo": "Promotions", "social": "Social",
        "spam_phishing": "Spam / phishing"}


def _describe_when(w: dict) -> str:
    parts = []
    if "thread_id" in w:
        parts.append(f"this conversation ({w['thread_id']})")
    if "from_address" in w:
        parts.append(f"mail from {w['from_address']}")
    if "from_domain" in w:
        parts.append(f"anyone at {w['from_domain']}")
    if "from_domain_any" in w:
        parts.append("anyone at " + " / ".join(w["from_domain_any"]))
    if "from_name_any" in w:
        parts.append("sender name containing " + " or ".join(repr(x) for x in w["from_name_any"]))
    if "subject_any" in w:
        parts.append("subject like " + " or ".join(repr(x) for x in w["subject_any"]))
    if "body_any" in w:
        parts.append("body mentioning " + " or ".join(repr(x) for x in w["body_any"]))
    if "text_any" in w:
        parts.append("text mentioning " + " or ".join(repr(x) for x in w["text_any"]))
    if "from_domain_not_any" in w:
        parts.append("not from " + " / ".join(w["from_domain_not_any"]))
    if "is_list" in w:
        parts.append("mailing-list mail" if w["is_list"] else "personal (non-list) mail")
    return ", ".join(parts) or "any mail"


def _describe_set(st: dict, exclude: bool) -> str:
    if exclude:
        parts = []
        if "category" in st:
            parts.append(f"label {_CAT.get(st['category'], st['category'])}")
        if "importance" in st:
            parts.append(f"mark {_IMP.get(st['importance'])}")
        for k, yes, no in (("needs_reply", "say it needs a reply", "say it needs no reply"),
                           ("suspicious", "flag it as suspicious", "clear the suspicious flag")):
            if k in st:
                parts.append(yes if st[k] else no)
        return "never " + " or ".join(parts)
    parts = []
    if "importance" in st:
        parts.append(_IMP.get(st["importance"], str(st["importance"])))
    if "min_importance" in st:
        parts.append(f"at least {_IMP.get(st['min_importance'])}")
    if "max_importance" in st:
        parts.append(f"at most {_IMP.get(st['max_importance'])}")
    if "category" in st:
        parts.append(f"label {_CAT.get(st['category'], st['category'])}")
    if "needs_reply" in st:
        parts.append("needs a reply" if st["needs_reply"] else "no reply needed")
    if st.get("suspicious"):
        parts.append("flag as suspicious")
    return ", ".join(parts)


def describe_rule(r: dict) -> str:
    """One line, e.g. "mail from news@x.example, subject like 'x weekly' -> not
    important (junk), label Newsletter"."""
    return (f"{_describe_when(r.get('when') or {})} -> "
            f"{_describe_set(r.get('set') or {}, rule_effect(r) == 'exclude')}")


def render_learned(prefs: dict, min_threads: int = PROMOTE_MIN_THREADS) -> str:
    """A plain-text "what I learned" digest, grouped by provenance (rules editor / digest)."""
    groups = [
        ("Rules you set", lambda r: rule_source(r) == "user" and rule_status(r) == "active"),
        ("Learned from your corrections",
         lambda r: rule_source(r) == "correction" and rule_status(r) == "active"),
        ("Learned from what you did in the inbox",
         lambda r: rule_source(r) == "implicit" and rule_status(r) == "active"),
        ("Learned from my own consistent decisions",
         lambda r: rule_source(r) == "agent" and rule_status(r) == "active"),
        ("Still learning (not applied yet)", lambda r: rule_status(r) == "candidate"),
        ("Turned off", lambda r: rule_status(r) == "disabled"),
    ]
    rules = prefs.get("rules", [])
    lines = ["What I learned about your mail", ""]
    if prefs.get("notes"):
        lines.append("Things you told me:")
        lines += [f"  - {n}" for n in prefs["notes"]]
        lines.append("")
    any_rule = False
    for title, pred in groups:
        rows = [r for r in rules if pred(r)]
        if not rows:
            continue
        any_rule = True
        lines.append(f"{title} ({len(rows)}):")
        for r in rows:
            sentence = describe_rule(r)
            meta = [f"scope {rule_scope(r)}"]
            if r.get("hits"):
                meta.append(f"used {r['hits']}x")
            if rule_status(r) == "candidate":
                p = candidate_progress(r, min_threads)
                meta.append(f"{p['threads']}/{p['needed']} conversations")
                meta.append("model agrees" if p["model_agreed"] else "waiting for model agreement")
                if p["contradictions"]:
                    meta.append(f"{p['contradictions']} contradiction(s)")
            if r.get("created"):
                meta.append(f"since {r['created'][:10]}")
            lines.append(f"  - {sentence[0].upper() + sentence[1:]}. ({'; '.join(meta)}) [{r.get('id')}]")
            if r.get("note"):
                lines.append(f"      why: {r['note']}")
        lines.append("")
    conflicts = find_conflicts(prefs)
    if conflicts:
        lines.append(f"Conflicts ({len(conflicts)}), the newer rule wins:")
        for c in conflicts:
            lines.append(f"  - {c['rules'][0]} vs {c['rules'][1]} on {', '.join(c['fields'])}: "
                         f"using {c['winner']}")
        lines.append("")
    if not any_rule:
        lines.append("Nothing yet: correct a few emails and I will start learning.")
    return "\n".join(lines).rstrip() + "\n"


def iter_active(prefs: dict) -> Iterable[dict]:
    return (r for r in prefs.get("rules", []) if rule_status(r) == "active")
