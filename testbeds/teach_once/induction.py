"""
induction.py — demonstration trace(s) -> an EXECUTABLE, versioned skill program,
plus "halt -> show me the fix -> vN+1" (WP5).

    res = induce([trace])                    # one or more demos of the same task
    if res.refused:                          # underdetermined: never guess
        print(res.record_next)               # e.g. "Record another example where ..."
    program = res.program                    # schema awos.skill_program/1 (JSON)
    batch = propose_params(program)          # ONE batch of field-label proposals to confirm
    program = apply_param_decisions(program, {"username": {"action": "constant"}})
    out = teach(program, halt_report, fix_trace, gate=gate_fn)   # -> {"status": "promoted", "program": vN+1}

What induction infers (deterministic, structural, ZERO model calls):

  parameters   values the user produced that VARY (across items or demos) and are
               explained by something the user saw (compiler._derive) become typed
               parameters (text / date / money / slug / context-date). Each comes
               with a field-label name proposal; propose_params() returns them as one
               batch and apply_param_decisions() applies confirm / rename / secret /
               constant / input. Constant typed values stay constants (fail-closed).
  loops        the per-item block of the demo (one block per item the user handled)
               becomes a loop over a SOURCE: the files of a folder, or the row links
               of a table on a page (pagination is followed at run time).
  item layouts every handled item yields a LAYOUT PROFILE: per parameter, line
               "skeletons" (the line the value was read from, with the value as a
               capture group and every other value-like span wildcarded) or a table
               cell position. Items whose layouts differ become guarded branches:
               at run time exactly the profile(s) whose anchors all match apply.
  divergence   aligned demos (difflib over op signatures) that differ on a
               non-consequential op (switch/open/read) -> optional op; on a
               consequential op (type/click/move/append/write) -> REFUSE with a
               "record another example" prompt. A value typed per item that nothing
               on screen explains, and that changes between items -> REFUSE.
  agent steps  only where content genuinely changes per run: when every demo item
               of the same kind had its own layout, the program declares an explicit,
               bounded `agent` step (extract the item's typed values) that the runner
               calls ONLY when no learned layout matches and an agent is plugged in.
               Healthy replays never call it.

Halt -> teach (teach()): a runner halt report + a SHORT fix demonstration trace:

  missing/ambiguous control, fix = the same action on another control -> alias
  unexpected screen (dialog / interstitial), fix = clicks       -> guarded handler
                                                                   (guard = text that is
                                                                   new on that screen +
                                                                   the clicked control)
  unmatched item, fix = the user handled it                     -> new layout profile
  unmatched item, fix = the user left it alone                  -> taught skip guard

  The candidate is re-gated by a pluggable gate (testbed: the demo + a canary
  instance) and only then emitted as version N+1 with provenance (parent sha,
  halt, fix-trace sha, change, gate summary). Refusals leave vN unchanged.

Third-party: several algorithms are ported (re-implemented against this testbed's
trace/skill format, no code copied verbatim) from OpenAdapt flow,
packages/flow/openadapt_flow/{compiler/induction.py, compiler/param_confirm.py,
learning/teach.py, learning/halt_loop.py, learning/gate.py, learning/library.py}:
    MIT License, Copyright (c) 2026 OpenAdapt.AI (MLDSAI Inc.)
    Permission is hereby granted, free of charge, to any person obtaining a copy of
    this software ... (full text: docs/third_party/wp5.md)
workflow-use (AGPL-3.0) informed the design of the explicit bounded agent step only;
no workflow-use code was read into or copied into this file.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .common import DATE_FORMATS, csv_dicts, find_dates, fmt_money, parse_date, parse_money, slug
from .compiler import DESTRUCTIVE_RE, _derive, _name, compile_trace
from .trace import Trace

PROGRAM_SCHEMA = "awos.skill_program/1"
ROLE = {"AXLink": "link", "AXButton": "button", "AXTextField": "textbox"}
CONSEQUENTIAL = {"type", "click", "download", "move", "append_rows", "write_file", "mkdir", "delete", "edit_file"}

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
URL_RE = re.compile(r"https?://\S+")
MONEY_SPAN_RE = re.compile(r"(?:[$€£]|EUR|USD)\s?-?\d[\d,.]*\d|-?\d[\d,]*[.,]\d{2}(?!\d)")
DIGIT_TOKEN_RE = re.compile(r"[\w-]*\d[\w-]*")
MONEY_FULL_RE = re.compile(r"(?:[$€£]|EUR|USD)?\s?-?\d[\d,.]*")
STOPWORDS = {"the", "a", "an", "of", "to", "and", "or", "for", "in", "on", "by"}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def canonical_sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def program_sha(program: dict) -> str:
    return canonical_sha(program)


def mask_path(path: Optional[str]) -> str:
    return re.sub(r"\d+", "#", (path or "").split("?")[0])


def line_hash(line: str) -> str:
    return hashlib.sha1(re.sub(r"\s+", " ", line.strip().lower()).encode()).hexdigest()[:12]


def _mask_value(v: str) -> str:
    v = str(v)
    return v if len(v) <= 2 else v[0] + "*" * (len(v) - 2) + v[-1]


# ======================================================== name matching ==

def norm_name(s: Optional[str]) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (s or "").lower()))


def name_tokens(s: Optional[str]) -> set:
    return {t for t in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(t) > 1 and t not in STOPWORDS}


def fuzzy_pick(name: str, candidates: List[str], exclude=()) -> Tuple[str, List[int]]:
    """Unique-best token-overlap match among same-role candidates.

    Candidates that are the exact target of ANOTHER step of the skill are excluded
    (the demo showed they mean something else); destructive controls are never
    picked for a non-destructive target. Returns ("ok", [i]) | ("ambiguous", [i..]) |
    ("missing", [])."""
    want = name_tokens(name)
    if not want:
        return "missing", []
    excl = {norm_name(x) for x in exclude}
    destructive_target = bool(re.search(DESTRUCTIVE_RE, name or "", re.I))
    scored = []
    for i, c in enumerate(candidates):
        if norm_name(c) in excl:
            continue
        if not destructive_target and re.search(DESTRUCTIVE_RE, c or "", re.I):
            continue
        s = len(want & name_tokens(c))
        if s:
            scored.append((s, i))
    if not scored:
        return "missing", []
    best = max(s for s, _ in scored)
    tops = [i for s, i in scored if s == best]
    return ("ok" if len(tops) == 1 else "ambiguous"), tops


# ============================================================ skeletons ==

def _spans(line: str, raw: Optional[str], others: List[str]) -> Optional[List[tuple]]:
    spans: List[tuple] = []
    if raw is not None:
        i = line.find(raw)
        if i < 0:
            return None
        spans.append((i, i + len(raw), "v"))

    def add(a: int, b: int) -> None:
        if a >= b or any(a < e and s < b for s, e, _ in spans):
            return
        spans.append((a, b, "*"))

    for o in sorted({o for o in others if o and o != raw}, key=len, reverse=True):
        for m in re.finditer(re.escape(o), line):
            add(m.start(), m.end())
    for rx in (EMAIL_RE, URL_RE):
        for m in rx.finditer(line):
            add(m.start(), m.end())
    for r, _d in find_dates(line):
        for m in re.finditer(re.escape(r), line):
            add(m.start(), m.end())
    for rx in (MONEY_SPAN_RE, DIGIT_TOKEN_RE):
        for m in rx.finditer(line):
            add(m.start(), m.end())
    return sorted(spans)


def _lit_re(s: str) -> str:
    return "".join(r"\s+" if tok.isspace() else re.escape(tok) for tok in re.split(r"(\s+)", s) if tok)


def skeleton(line: str, raw: Optional[str], others: List[str] = ()) -> Optional[dict]:
    """A line as a regex: the value -> (?P<v>.+?), other value-like spans -> .+?, rest literal."""
    line = line.strip()
    sp = _spans(line, raw, list(others))
    if sp is None:
        return None
    parts, human, literal, pos = [], [], "", 0
    for s, e, k in sp:
        lit = line[pos:s]
        parts.append(_lit_re(lit))
        human.append(lit)
        literal += lit
        parts.append("(?P<v>.+?)" if k == "v" else ".+?")
        human.append("{value}" if k == "v" else "{…}")
        pos = e
    parts.append(_lit_re(line[pos:]))
    human.append(line[pos:])
    literal += line[pos:]
    return {"re": "".join(parts), "text": "".join(human), "letters": len(re.findall(r"[A-Za-z]", literal))}


# ========================================================= value typing ==

def text_shape(examples: List[str]) -> dict:
    ex = [str(e) for e in examples if str(e)]
    if not ex:
        return {}
    return {"no_space": all(not re.search(r"\s", e) for e in ex),
            "digits_only": all(e.isdigit() for e in ex),
            "has_digit": all(re.search(r"\d", e) for e in ex),
            "has_alpha": all(re.search(r"[A-Za-z]", e) for e in ex)}


def raw_date_format(raw: str, iso: Optional[str] = None) -> Optional[str]:
    for f in DATE_FORMATS:
        d = parse_date(raw, (f,))
        if d and (iso is None or d.isoformat() == iso):
            return f
    return None


def normalize_value(ptype: str, raw: str, param: dict) -> Tuple[Optional[str], str]:
    """Typed, validated value from a captured string -> (value, '' | 'invalid' | 'ambiguous')."""
    raw = (raw or "").strip()
    if not raw:
        return None, "invalid"
    if ptype == "date":
        hits: Dict[dt.date, str] = {}
        for f in DATE_FORMATS:
            d = parse_date(raw, (f,))
            if d:
                hits.setdefault(d, f)
        if not hits:
            return None, "invalid"
        if len(hits) > 1:          # e.g. 04/05/2026: only the demo's own format may decide
            for f in param.get("raw_formats", []):
                d = parse_date(raw, (f,))
                if d:
                    return d.isoformat(), ""
            return None, "ambiguous"
        return next(iter(hits)).isoformat(), ""
    if ptype == "money":
        if not MONEY_FULL_RE.fullmatch(raw):
            return None, "invalid"
        d = parse_money(raw)
        return (fmt_money(d), "") if d is not None else (None, "invalid")
    if ptype == "slug":
        s = slug(raw)
        return (s, "") if s else (None, "invalid")
    sh = param.get("shape", {})
    if sh.get("no_space") and re.search(r"\s", raw):
        return None, "invalid"
    if sh.get("digits_only") and not raw.isdigit():
        return None, "invalid"
    if sh.get("has_digit") and not re.search(r"\d", raw):
        return None, "invalid"
    if sh.get("has_alpha") and not re.search(r"[A-Za-z]", raw):
        return None, "invalid"
    return raw, ""


def _same_value(ptype: str, cell: str, value: str) -> bool:
    if ptype == "money":
        a = parse_money(cell)
        return a is not None and a == Decimal(value)
    if ptype == "date":
        d = parse_date(cell)
        return d is not None and d.isoformat() == value
    return (cell or "").strip() == value


# ============================================================== profiles ==

def _csv_data_lines(lines: List[str]) -> set:
    rows = [i for i, ln in enumerate(lines) if ln and not ln.startswith("#")]
    return set(rows[1:])


def build_profile(text: str, kind: str, raws: Dict[str, tuple]) -> Tuple[dict, List[str]]:
    """Layout profile of ONE item. raws: {param: (type, raw text as seen, normalized value)}."""
    lines = [ln.strip() for ln in (text or "").splitlines()]
    is_csv = kind == ".csv"
    table = csv_dicts(text) if is_csv else None
    data_lines = _csv_data_lines(lines) if is_csv else set()
    anchors: Dict[str, list] = {}
    missing = []
    for name, (ptype, raw, value) in raws.items():
        al: List[dict] = []
        if table:
            hits = [(ri, col) for ri, row in enumerate(table) for col, cell in row.items()
                    if col and cell is not None and _same_value(ptype, cell, value)]
            if len(hits) == 1:
                ri, col = hits[0]
                al.append({"kind": "cell", "column": col,
                           "row": "last" if ri == len(table) - 1 else "first" if ri == 0 else ri})
        others = [r for n, (_t, r, _v) in raws.items() if n != name]
        lit, idx = [], []
        for i, ln in enumerate(lines):
            if not ln or raw not in ln or i in data_lines:
                continue
            sk = skeleton(ln, raw, others)
            if sk is None:
                continue
            a = {"kind": "line", "re": sk["re"], "text": sk["text"]}
            if sk["letters"] >= 2:
                if all(x["re"] != a["re"] for x in lit):
                    lit.append(a)
            else:
                idx.append(dict(a, line_index=i))
        al += lit
        if not al and idx:
            al.append(idx[0])           # e.g. the bank name alone on the first line of a PDF
        if not al:
            missing.append(name)
        anchors[name] = al
    return {"kind": kind, "anchors": anchors}, missing


def _anchor_values(anchor: dict, lines: List[str], table, param: dict) -> Tuple[set, bool]:
    vals, amb = set(), False
    if anchor["kind"] == "cell":
        if not table:
            return vals, amb
        pos = anchor["row"]
        idx = len(table) - 1 if pos == "last" else 0 if pos == "first" else int(pos)
        if 0 <= idx < len(table) and anchor["column"] in table[idx]:
            v, prob = normalize_value(param["type"], table[idx][anchor["column"]] or "", param)
            if v is not None:
                vals.add(v)
            amb = prob == "ambiguous"
        return vals, amb
    rx = re.compile(anchor["re"])
    targets = lines
    if anchor.get("line_index") is not None:
        i = anchor["line_index"]
        targets = [lines[i]] if 0 <= i < len(lines) else []
    for ln in targets:
        m = rx.fullmatch(ln)
        if not m:
            continue
        v, prob = normalize_value(param["type"], m.group("v"), param)
        if v is not None:
            vals.add(v)
        elif prob == "ambiguous":
            amb = True
    return vals, amb


def match_profile(profile: dict, text: str, params: Dict[str, dict], kind: str) -> dict:
    """-> {"status": ok|nomatch|ambiguous, "values": {...}, "why": str}. Never picks between values."""
    lines = [ln.strip() for ln in (text or "").splitlines()]
    table = csv_dicts(text) if kind == ".csv" else None
    values = {}
    for name, anchors in profile["anchors"].items():
        p = params[name]
        found, amb = set(), False
        for a in anchors:
            vs, aa = _anchor_values(a, lines, table, p)
            found |= vs
            amb |= aa
        if amb or len(found) > 1:
            return {"status": "ambiguous", "values": values, "why": f"{name}: {sorted(found) or 'ambiguous date'}"}
        if not found:
            return {"status": "nomatch", "values": values, "why": f"{name}: no anchor matched"}
        values[name] = found.pop()
    return {"status": "ok", "values": values, "why": ""}


def guard_lines(text: str, seen: set, prefer: List[str] = (), n: int = 2) -> List[dict]:
    """Lines that are NEW (not seen in any demo observation of the app) as wildcarded regexes."""
    out = []
    pool = [p for p in prefer if p] + [ln.strip() for ln in (text or "").splitlines()]
    for ln in pool:
        if not ln or line_hash(ln) in seen:
            continue
        sk = skeleton(ln, None)
        if sk and sk["letters"] >= 8 and all(o["re"] != sk["re"] for o in out):
            out.append({"re": sk["re"], "text": sk["text"]})
        if len(out) >= n:
            break
    return out


# ============================================================ induction ==

@dataclass
class InductionResult:
    program: Optional[dict]
    refused: bool = False
    uncertainties: List[dict] = field(default_factory=list)
    record_next: List[str] = field(default_factory=list)
    proposals: List[dict] = field(default_factory=list)
    decisions: Dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"refused": self.refused, "uncertainties": self.uncertainties, "record_next": self.record_next,
                "proposals": self.proposals, "decisions": self.decisions, "program": self.program}


def _unc(kind: str, where: str, detail: str, record_next: str) -> dict:
    return {"kind": kind, "where": where, "detail": detail, "record_next": record_next}


def _post_of(step) -> dict:
    if step.action == "download":
        return {"download": True}
    a = step.after or {}
    if a.get("url_path") is None:
        return {}
    return {"url": [mask_path(a["url_path"])], "no_alerts": not a.get("alerts"), "status_ok": True}


def _trace_sha(trace: Trace) -> str:
    return canonical_sha(trace.to_dict())


def _seen_text(trace: Trace) -> Dict[str, List[str]]:
    out: Dict[str, set] = {}
    for s in trace.steps:
        for win in (s.after or {}, s.before or {}):
            if not win:
                continue
            bag = out.setdefault(s.app, set())
            for ln in (win.get("page_text") or "").splitlines() + [win.get("title") or ""] + list(win.get("alerts") or []):
                if ln.strip():
                    bag.add(line_hash(ln))
    return {a: sorted(v) for a, v in out.items()}


def _ops_from(trace: Trace, sk: dict, part: List[dict], steps: Dict[int, Any]) -> List[dict]:
    ops = []
    for g in part:
        st = steps[g["step"]]
        op = {k: copy.deepcopy(v) for k, v in g.items() if k not in ("step", "recovery")}
        op["id"] = f"s{g['step']}"
        op["step"] = g["step"]
        if g.get("recovery"):
            op["recovery"] = True
        tgt = op.get("target") or {}
        if "select" in tgt:
            op["loop_select"] = True
        if st.action in ("click", "download", "open_url"):
            op["post"] = _post_of(st)
        if st.action == "type" and isinstance(st.value, dict) and "secret" in st.value:
            op["value"] = {"secret": st.value["secret"]}
        if st.action == "append_rows" and isinstance(op.get("rows"), dict):
            spec = next((a for a in sk["aggregates"] if a["path"] == tgt.get("path")), None)
            op["aggregate"] = spec
            op.pop("rows", None)
        if st.action == "write_file":
            fc = [c for c in sk["check"] if c["kind"] == "file_created"]
            hit = next((c for c in fc if c["path"] == tgt.get("path")), fc[0] if len(fc) == 1 else None)
            op["lines"] = list(hit["contains_lines"]) if hit else [ln for ln in (st.value or "").splitlines() if ln.strip()]
        ops.append(op)
    # a failed submit followed by a recovery retype: the program types the normalized value
    # up front, so the rejected attempt and the retype collapse into the successful pair
    out: List[dict] = []
    i = 0
    while i < len(ops):
        o = ops[i]
        nxt = ops[i + 1] if i + 1 < len(ops) else None
        if (o["action"] == "click" and o.get("post", {}).get("no_alerts") is False and nxt is not None
                and nxt["action"] == "type" and nxt.get("recovery")):
            i += 2
            continue
        out.append(o)
        i += 1
    return out


def _param_specs(sk: dict, ops: List[dict]) -> List[dict]:
    out = []
    labels = {}
    for o in ops:
        v = o.get("value")
        if o["action"] == "type" and isinstance(v, str) and re.fullmatch(r"\{[a-z0-9_]+\}", v):
            labels.setdefault(v[1:-1], o["target"].get("name"))
    for p in sk["parameters"]:
        ex = [e["value"] for e in p["examples"]]
        spec = {"name": p["name"], "type": p["type"], "scope": p["scope"],
                "field_label": labels.get(p["name"], p["name"].replace("_", " ")),
                "seen_near": p["source"].get("label", ""), "source": copy.deepcopy(p["source"]),
                "examples": ex, "confirmed": True}
        if p["type"] == "date":
            spec["raw_formats"] = sorted({f for e in p["examples"] if (f := raw_date_format(e.get("raw", ""), e["value"]))})
        if p["type"] == "text":
            spec["shape"] = text_shape(ex)
        out.append(spec)
    return out


def _program_from_trace(trace: Trace, sk: dict, *, agent_steps: bool = True) -> Tuple[dict, List[dict]]:
    steps = {s.i: s for s in trace.steps}
    unc: List[dict] = []
    setup = _ops_from(trace, sk, sk["procedure"]["setup"], steps)
    body = _ops_from(trace, sk, sk["procedure"]["for_each_item"], steps)
    tail = _ops_from(trace, sk, sk["procedure"]["finally"], steps)
    params = _param_specs(sk, setup + body + tail)
    pmap = {p["name"]: p for p in params}
    web_apps = sorted({a["name"] for a in trace.environment.get("apps", []) if a.get("kind") == "web"})

    globals_ = []
    for p in params:
        if p["scope"] != "global":
            continue
        if p["source"].get("source") == "context":
            globals_.append({"name": p["name"], "relation": p["source"]["relation"]})
        else:
            unc.append(_unc("global_from_observation", p["name"],
                            f"run-wide value {p['name']!r} comes from a single observation",
                            f"Record another example so I can see where '{p['field_label']}' comes from each time."))

    loop = None
    item_steps = sk["items"].get("steps", [])
    if item_steps and body:
        sel = body[0] if body[0].get("loop_select") else None
        first = steps[item_steps[0]]
        if sel is None:
            unc.append(_unc("loop_source", sel and sel["id"] or "", "items found but no item selection step",
                            "Record another example that starts each item by opening it."))
        else:
            if first.action == "read":
                source = {"kind": "folder", "folder": str(Path(first.target["path"]).parent)}
            else:
                cap = re.search(r"table '(.+?)'", sel["target"].get("select", ""))
                source = {"kind": "table_links", "app": first.app, "url_path": (first.before or {}).get("url_path") or "/",
                          "table": cap.group(1) if cap else ""}
            item_params = [p["name"] for p in params if p["scope"] == "item"]
            profiles = []
            for k in item_steps:
                st = steps[k]
                if st.action == "read":
                    text, kind = st.observed.get("text", ""), Path(st.target["path"]).suffix.lower()
                else:
                    text, kind = (st.after or {}).get("page_text", ""), mask_path((st.after or {}).get("url_path"))
                raws = {}
                for name in item_params:
                    ex = [e for e in next(q for q in sk["parameters"] if q["name"] == name)["examples"]
                          if e.get("item") == k]
                    if not ex:
                        unc.append(_unc("unexplained_item_value", name,
                                        f"for the item at demo step {k} nothing on screen explains '{pmap[name]['field_label']}'",
                                        f"Record another example: I can't see where '{pmap[name]['field_label']}' comes "
                                        f"from for every item (or mark it as a run input)."))
                        continue
                    raws[name] = (pmap[name]["type"], ex[-1].get("raw", ex[-1]["value"]), ex[-1]["value"])
                prof, missing = build_profile(text, kind, raws)
                for m in missing:
                    unc.append(_unc("unanchored_value", m, f"value of {m!r} not locatable on the item page",
                                    f"Record another example where '{pmap[m]['field_label']}' is visible on the item."))
                prof["from"] = {"instance": trace.instance, "demo_step": k}
                if prof["anchors"] and not missing and len(raws) == len(item_params):
                    profiles.append(prof)
            # per-item literal values that change between items without explanation
            bounds = sorted(item_steps + sk["items"].get("skipped_steps", []))

            def block(i: int) -> Optional[int]:
                cur = None
                for b in bounds:
                    if b <= i:
                        cur = b
                return cur
            for op in body:
                v = op.get("value")
                if op["action"] != "type" or not isinstance(v, str) or "{" in v:
                    continue
                per = {}
                for s in trace.steps:
                    if s.action == "type" and s.target.get("name") == op["target"].get("name") and block(s.i) in item_steps:
                        per[block(s.i)] = s.value
                if len(set(map(str, per.values()))) > 1:
                    vals = list(dict.fromkeys(map(str, per.values())))
                    unc.append(_unc("unexplained_varying_value", op["id"],
                                    f"'{op['target'].get('name')}' was {vals[0]!r} for one item and {vals[1]!r} for another",
                                    f"Record another example: nothing on screen explains the value typed into "
                                    f"'{op['target'].get('name')}' (or mark it as a parameter)."))
            kinds = {p["kind"] for p in profiles}
            on_unmatched = "skip" if sk["items"].get("unselected_examples") else "halt"
            loop = {"source": source, "params": item_params, "profiles": profiles, "body": body,
                    "skip_rules": copy.deepcopy(sk["skip_rules"]), "skip_unmatched": [],
                    "on_unmatched": on_unmatched,
                    "evidence": {"items": len(item_steps), "skipped": len(sk["items"].get("skipped_steps", [])),
                                 "unselected_examples": sk["items"].get("unselected_examples", [])}}
            if (agent_steps and on_unmatched == "halt" and len(profiles) >= 2 and len(kinds) == 1
                    and len({canonical_sha(p["anchors"]) for p in profiles}) == len(profiles)):
                loop["agent_step"] = {
                    "id": "extract_item", "kind": "agent", "when": "no learned layout matches the item",
                    "why": f"every one of the {len(profiles)} demo items had its own layout",
                    "returns": [{"name": n, "type": pmap[n]["type"], "label": pmap[n]["field_label"]} for n in item_params],
                    "max_calls_per_item": 1}
    elif body:
        setup, body = setup + body, []

    program = {
        "schema": PROGRAM_SCHEMA, "skill_id": trace.task, "task": trace.task, "version": 1, "parent": None,
        "note": trace.note, "apps": sk["apps"], "web_apps": web_apps,
        "params": params, "globals": globals_,
        "setup": setup, "loop": loop, "finally": tail, "handlers": [],
        "check": copy.deepcopy(sk["check"]), "invariants": copy.deepcopy(sk["invariants"]),
        "aggregates": copy.deepcopy(sk["aggregates"]),
        "policy": {"idempotent_effects": True, "dedupe_items": "params", "follow_pagination": True,
                   "fuzzy_names": "unique token overlap, excluding other steps' targets and destructive controls"},
        "seen_text": _seen_text(trace),
        "uncertainties": [], "notes": [],
        "provenance": [{"version": 1, "kind": "induced", "at": now_iso(),
                        "demos": [{"task": trace.task, "instance": trace.instance, "steps": len(trace.steps),
                                   "trace_sha256": _trace_sha(trace)}]}],
    }
    if len(sk["aggregates"]) and any(a.get("filter") for a in sk["aggregates"]):
        program["notes"].append("aggregate filter chosen as the first hypothesis consistent with the demo")
    return program, unc


def all_ops(program: dict) -> List[dict]:
    return list(program["setup"]) + list((program.get("loop") or {}).get("body", [])) + list(program["finally"])


# ---------------------------------------------------------------- merge --

def _op_sig(op: dict) -> tuple:
    t = op.get("target") or {}
    key = "<item>" if op.get("loop_select") else (t.get("name") or t.get("path") or t.get("url_path") or op.get("to") or "")
    if op["action"] == "open_url":
        key = mask_path(key)
    return (op["app"], op["action"], t.get("role", ""), key)


def _desc(op: dict) -> str:
    t = op.get("target") or {}
    return f"{op['action']} {t.get('name') or t.get('path') or t.get('url_path') or op.get('to') or ''} in {op['app']}".strip()


def _merge_op(x: dict, y: dict, unc: List[dict]) -> dict:
    out = copy.deepcopy(x)
    if x.get("value") != y.get("value"):
        a, b = x.get("value"), y.get("value")
        unc.append(_unc("unexplained_varying_value", x["id"],
                        f"'{(x.get('target') or {}).get('name')}' was {a!r} in one demo and {b!r} in another",
                        f"Record another example: I can't tell where the value for "
                        f"'{(x.get('target') or {}).get('name')}' comes from (or mark it as a parameter)."))
    for key in ("aggregate", "lines", "row", "to"):
        if x.get(key) != y.get(key):
            unc.append(_unc("divergent_effect", x["id"], f"{key} differs between demos for {_desc(x)}",
                            f"Record another example showing how '{_desc(x)}' should be filled in."))
    if x.get("post", {}).get("url") or y.get("post", {}).get("url"):
        out.setdefault("post", {})["url"] = sorted(set(x.get("post", {}).get("url", [])) | set(y.get("post", {}).get("url", [])))
    return out


def _merge_ops(seqs: List[List[dict]], where: str, unc: List[dict]) -> List[dict]:
    base = copy.deepcopy(seqs[0])
    for seq in seqs[1:]:
        sm = SequenceMatcher(a=[_op_sig(o) for o in base], b=[_op_sig(o) for o in seq], autojunk=False)
        out: List[dict] = []
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                out += [_merge_op(x, y, unc) for x, y in zip(base[i1:i2], seq[j1:j2])]
            elif tag in ("delete", "insert"):
                for o in (base[i1:i2] if tag == "delete" else seq[j1:j2]):
                    if o["action"] in CONSEQUENTIAL:
                        unc.append(_unc("optional_consequential_step", o["id"],
                                        f"'{_desc(o)}' ({where}) happens in some demonstrations but not others",
                                        f"Record another example that shows when to '{_desc(o)}' and when not to."))
                    out.append(dict(copy.deepcopy(o), optional=True))
            else:
                a = "; ".join(_desc(o) for o in base[i1:i2])
                b = "; ".join(_desc(o) for o in seq[j1:j2])
                unc.append(_unc("divergence", where, f"one demo did [{a}], another did [{b}]",
                                f"Record another example that shows when to do [{a}] instead of [{b}]."))
                out += copy.deepcopy(base[i1:i2])
        base = out
    return base


def _merge_programs(progs: List[dict], unc: List[dict]) -> dict:
    m = copy.deepcopy(progs[0])
    m["setup"] = _merge_ops([p["setup"] for p in progs], "setup", unc)
    m["finally"] = _merge_ops([p["finally"] for p in progs], "finally", unc)
    loops = [p["loop"] for p in progs if p.get("loop")]
    if loops and len(loops) != len(progs):
        unc.append(_unc("loop_presence", "loop", "some demonstrations handle a list of items and some do not",
                        "Record another example that handles at least one item."))
    if loops:
        L = copy.deepcopy(loops[0])
        if any(l["source"] != L["source"] for l in loops[1:]):
            unc.append(_unc("loop_source", "loop", "demos loop over different sources",
                            "Record another example that takes items from the same place."))
        L["body"] = _merge_ops([l["body"] for l in loops], "for each item", unc)
        seen = set()
        L["profiles"] = []
        for l in loops:
            for pr in l["profiles"]:
                key = canonical_sha([pr["kind"], pr["anchors"]])
                if key not in seen:
                    seen.add(key)
                    L["profiles"].append(pr)
        for l in loops[1:]:
            for r in l["skip_rules"]:
                if r not in L["skip_rules"]:
                    L["skip_rules"].append(r)
            if l.get("agent_step") and not L.get("agent_step"):
                L["agent_step"] = l["agent_step"]
        L["on_unmatched"] = "skip" if any(l["on_unmatched"] == "skip" for l in loops) else "halt"
        L["evidence"] = {"items": sum(l["evidence"]["items"] for l in loops),
                         "skipped": sum(l["evidence"]["skipped"] for l in loops), "demos": len(loops)}
        m["loop"] = L
    params: Dict[str, dict] = {}
    for p in progs:
        for q in p["params"]:
            have = params.get(q["name"])
            if have is None:
                params[q["name"]] = copy.deepcopy(q)
                continue
            if have["type"] != q["type"] or have["scope"] != q["scope"]:
                unc.append(_unc("param_type_conflict", q["name"], f"{have['type']} vs {q['type']}",
                                f"Record another example: '{q['field_label']}' looks different between demos."))
            have["examples"] = list(dict.fromkeys(have["examples"] + q["examples"]))
            if q.get("raw_formats"):
                have["raw_formats"] = sorted(set(have.get("raw_formats", [])) | set(q["raw_formats"]))
            if have["type"] == "text":
                have["shape"] = text_shape(have["examples"])
    m["params"] = list(params.values())
    gl = {canonical_sha(g) for g in progs[0]["globals"]}
    for p in progs[1:]:
        if {canonical_sha(g) for g in p["globals"]} != gl:
            unc.append(_unc("globals_conflict", "globals", "run-wide values come from different places",
                            "Record another example; the demos disagree on what the run-wide dates are."))
    for p in progs[1:]:
        for app, hs in p["seen_text"].items():
            m["seen_text"][app] = sorted(set(m["seen_text"].get(app, [])) | set(hs))
    m["provenance"] = [{"version": 1, "kind": "induced", "at": now_iso(),
                        "demos": [d for p in progs for d in p["provenance"][0]["demos"]]}]
    return m


def induce(traces: List[Trace], *, confirm: Optional[Callable[[List[dict]], Dict[str, dict]]] = None,
           agent_steps: bool = True) -> InductionResult:
    """N demonstration traces of one task -> InductionResult (program or an honest refusal)."""
    traces = [t if isinstance(t, Trace) else Trace.load(t) for t in traces]
    if not traces:
        return InductionResult(None, True, [], ["Record a demonstration first."])
    if len({t.task for t in traces}) != 1:
        return InductionResult(None, True, [_unc("mixed_tasks", "", "traces of different tasks", "")],
                               ["Record demonstrations of one task at a time."])
    unc: List[dict] = []
    progs = []
    for t in traces:
        p, u = _program_from_trace(t, compile_trace(t), agent_steps=agent_steps)
        progs.append(p)
        unc += u
    program = progs[0] if len(progs) == 1 else _merge_programs(progs, unc)
    if unc:
        rn = list(dict.fromkeys(u["record_next"] for u in unc if u["record_next"]))
        return InductionResult(None, True, unc, rn)
    proposals = propose_params(program)
    decisions: Dict[str, dict] = {}
    if confirm is not None:
        decisions = confirm(proposals) or {}        # ONE batch, one call
        program = apply_param_decisions(program, decisions)
    program["confirmed_params"] = {"asked": len(proposals) if confirm else 0, "decisions": decisions}
    return InductionResult(program, False, [], [], proposals, decisions)


# ===================================================== param confirmation ==

def propose_params(program: dict) -> List[dict]:
    """Field-label parameter proposals as ONE batch (values masked)."""
    out = []
    for p in program["params"]:
        src = p.get("source", {})
        ev = (f"computed from the clock: {src['relation']}" if src.get("source") == "context"
              else f"read from what you saw near '{p.get('seen_near', '')}' (changes per {p['scope']})")
        out.append({"name": p["name"], "field_label": p.get("field_label"), "type": p["type"], "scope": p["scope"],
                    "example": _mask_value(p["examples"][0]) if p.get("examples") else "", "evidence": ev,
                    "kind": "derived", "default": "confirm"})
    for op in all_ops(program):
        v = op.get("value")
        if op["action"] == "type" and isinstance(v, str) and "{" not in v:
            out.append({"name": _name(op["target"].get("name", "value")), "field_label": op["target"].get("name"),
                        "type": "text", "scope": "global", "example": _mask_value(v), "op": op["id"],
                        "evidence": "typed the same value every time and nothing on screen explains it",
                        "kind": "constant_field", "default": "constant"})
    return out


def _rename_strings(obj: Any, old: str, new: str) -> Any:
    pat = re.compile(r"\{" + re.escape(old) + r"(?=[:}])")
    if isinstance(obj, str):
        return pat.sub("{" + new, obj)
    if isinstance(obj, list):
        return [_rename_strings(x, old, new) for x in obj]
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            v = _rename_strings(v, old, new)
            if k == "param" and v == old:
                v = new
            out[k] = v
        return out
    return obj


def apply_param_decisions(program: dict, decisions: Dict[str, dict]) -> dict:
    """decisions: {proposed_name: {"action": confirm|rename|secret|constant|input, "name": new_name}}."""
    prog = copy.deepcopy(program)
    for prop in propose_params(program):
        d = decisions.get(prop["name"])
        if not d:
            continue
        act = d.get("action", "confirm")
        if prop["kind"] == "derived":
            if act == "rename" and d.get("name") and d["name"] != prop["name"]:
                old, new = prop["name"], _name(d["name"])
                prog = _rename_strings(prog, old, new)
                for p in prog["params"]:
                    if p["name"] == old:
                        p["name"] = new
                for g in prog["globals"]:
                    if g["name"] == old:
                        g["name"] = new
                if prog.get("loop"):
                    L = prog["loop"]
                    L["params"] = [new if x == old else x for x in L["params"]]
                    for pr in L["profiles"]:
                        if old in pr["anchors"]:
                            pr["anchors"][new] = pr["anchors"].pop(old)
            elif act == "secret":
                for p in prog["params"]:
                    if p["name"] == prop["name"]:
                        p["secret"], p["examples"] = True, []
            elif act == "constant" and prop["scope"] == "global":
                p = next(q for q in prog["params"] if q["name"] == prop["name"])
                val = p["examples"][0]
                prog = _subst_constant(prog, p["name"], val, p["type"])
                prog["params"] = [q for q in prog["params"] if q["name"] != p["name"]]
                prog["globals"] = [g for g in prog["globals"] if g["name"] != p["name"]]
        else:      # a constant typed into a field
            op = next(o for o in all_ops(prog) if o["id"] == prop["op"])
            if act in ("confirm", "rename", "input"):
                name = _name(d.get("name") or prop["name"])
                op["value"] = "{" + name + "}"
                prog["params"].append({"name": name, "type": "text", "scope": "input", "field_label": prop["field_label"],
                                       "source": {"source": "run_input"}, "examples": [], "confirmed": True})
            elif act == "secret":
                op["value"] = {"secret": prop["field_label"]}
    return prog


def _subst_constant(obj: Any, name: str, value: str, ptype: str) -> Any:
    pat = re.compile(r"\{" + re.escape(name) + r"(?::([^}]+))?\}")

    def rep(m):
        if m.group(1) and ptype == "date":
            return dt.date.fromisoformat(value).strftime(m.group(1))
        return value
    if isinstance(obj, str):
        return pat.sub(rep, obj)
    if isinstance(obj, list):
        return [_subst_constant(x, name, value, ptype) for x in obj]
    if isinstance(obj, dict):
        return {k: _subst_constant(v, name, value, ptype) for k, v in obj.items()}
    return obj


# ================================================================ teach ==

class TeachRefusal(Exception):
    pass


def find_op(program: dict, op_id: str) -> Optional[dict]:
    return next((o for o in all_ops(program) if o["id"] == op_id), None)


def _fix_actions(fix: Trace) -> list:
    return [s for s in fix.steps if s.action not in ("switch_app", "open_url", "read")]


def _teach_ui(cand: dict, halt: dict, fix: Trace) -> dict:
    op = find_op(cand, halt.get("op_id", ""))
    acts = _fix_actions(fix)
    if not acts:
        raise TeachRefusal("the fix demonstration has no actions")
    reason = halt["reason"]
    controls = {(c["role"], c["name"]) for c in (halt.get("observed") or {}).get("controls", [])}
    first = acts[0]
    f_role, f_name = ROLE.get(first.target.get("role"), first.target.get("role")), first.target.get("name")
    for s in acts:
        if re.search(DESTRUCTIVE_RE, s.target.get("name", "") or "", re.I):
            raise TeachRefusal(f"the fix uses a destructive control ({s.target.get('name')!r}); "
                               "I won't learn that automatically")
    if op is not None and reason in ("missing_control", "ambiguous_match") and op["action"] in ("type", "click", "download"):
        o_role = ROLE.get(op["target"].get("role"), op["target"].get("role"))
        same_kind = first.action == op["action"] or {first.action, op["action"]} <= {"click", "download"}
        if same_kind and f_role == o_role and f_name and len(acts) == 1:
            if op["action"] == "type" and str(first.value) != str(halt.get("intended_value")):
                raise TeachRefusal("the fix typed a different value than the step intended; record the step again")
            if (o_role, f_name) not in controls and controls:
                raise TeachRefusal(f"{f_name!r} was not on the screen where the run stopped")
            if f_name == op["target"].get("name") or f_name in op["target"].get("aliases", []):
                raise TeachRefusal("the fix used the same control the step already targets")
            op["target"].setdefault("aliases", []).append(f_name)
            return {"kind": "alias", "op": op["id"], "for": op["target"].get("name"), "alias": f_name}
    if reason == "ambiguous_match":
        raise TeachRefusal("the fix did not show which of the matching controls to use")
    new = list((halt.get("new_text") or []))
    if not new:
        raise TeachRefusal("nothing on the stopped screen was new, so I can't tell when to apply this fix")
    for s in acts:
        if s.action != "click":
            raise TeachRefusal("a fix for an unexpected screen may only click (dismiss / continue); "
                               "record a new demonstration for anything else")
        role = ROLE.get(s.target.get("role"), s.target.get("role"))
        if controls and (role, s.target.get("name")) not in controls and s is first:
            raise TeachRefusal(f"{s.target.get('name')!r} was not on the screen where the run stopped")
    app = (halt.get("observed") or {}).get("app") or (op or {}).get("app")
    handler = {"id": f"h{len(cand['handlers']) + 1}", "app": app,
               "guard": {"text_all": new[:2], "control": {"role": first.target.get("role"), "name": f_name}},
               "do": [{"action": "click", "target": {"role": s.target.get("role"), "name": s.target.get("name")}}
                      for s in acts],
               "learned_at": {"op": halt.get("op_id"), "reason": reason}, "then": "retry the step"}
    cand["handlers"].append(handler)
    return {"kind": "handler", "handler": handler["id"], "guard": handler["guard"], "do": [d["target"]["name"] for d in handler["do"]]}


def _teach_item(cand: dict, halt: dict, fix: Trace) -> dict:
    L = cand.get("loop")
    if not L:
        raise TeachRefusal("this skill has no item loop")
    obs = halt.get("observed") or {}
    text, kind = obs.get("text") or "", (halt.get("item") or {}).get("kind")
    acts = _fix_actions(fix)
    effectful = [s for s in acts if s.action in ("type", "append_rows", "move", "write_file", "download")]
    pmap = {p["name"]: p for p in cand["params"]}
    if not effectful:
        seen = set(cand["seen_text"].get(obs.get("app") or "", []))
        guard = guard_lines(text, seen, prefer=obs.get("headings") or [])
        if not guard:
            raise TeachRefusal("I can't find anything distinctive on this item to recognise it next time")
        g = {"kind": kind, "lines": guard, "evidence": "user left this item alone in a fix demonstration"}
        L["skip_unmatched"].append(g)
        return {"kind": "skip_guard", "guard": g}
    fields: Dict[str, str] = {}
    rows_map: Dict[str, Dict[int, str]] = {}
    for op in L["body"]:
        v = op.get("value")
        if op["action"] == "type" and isinstance(v, str) and re.fullmatch(r"\{[a-z0-9_]+\}", v):
            for n in [op["target"].get("name")] + op["target"].get("aliases", []):
                fields[n] = v[1:-1]
        if op["action"] == "append_rows" and op.get("row"):
            rows_map[op["target"]["path"]] = {j: c[1:-1] for j, c in enumerate(op["row"])
                                              if re.fullmatch(r"\{[a-z0-9_]+\}", c)}
    values: Dict[str, str] = {}
    for s in effectful:
        if s.action == "type" and isinstance(s.value, str):
            name = s.target.get("name", "")
            p = fields.get(name)
            if p is None:
                st, idx = fuzzy_pick(name, list(fields))
                p = fields[list(fields)[idx[0]]] if st == "ok" else None
            if p:
                values[p] = s.value
        if s.action == "append_rows" and s.value and s.target.get("path") in rows_map:
            for j, p in rows_map[s.target["path"]].items():
                if j < len(s.value[0]):
                    values[p] = s.value[0][j]
    missing = [p for p in L["params"] if p not in values]
    if missing:
        raise TeachRefusal(f"the fix did not show {', '.join(missing)}; record the whole item")
    ob = [{"step": 0, "app": obs.get("app"), "text": text, "table": csv_dicts(text) if kind == ".csv" else None,
           "kind": "document" if kind and kind.startswith(".") else "page", "path": (halt.get("item") or {}).get("ref")}]
    raws = {}
    for p, v in values.items():
        d = _derive(v, ob, {})
        ptype = pmap[p]["type"]
        if not d or d.get("source") != "observation":
            raise TeachRefusal(f"I can't see where {v!r} ({pmap[p]['field_label']}) comes from on this item; "
                               "record another example")
        ok = d["type"] == ptype or (ptype == "slug" and d["type"] in ("text", "slug")) or \
            (ptype == "text" and d["type"] in ("slug",))
        if not ok:
            raise TeachRefusal(f"{pmap[p]['field_label']} looks like a {d['type']} here, not a {ptype}")
        raws[p] = (ptype, d.get("raw", v), v)
    prof, miss = build_profile(text, kind, raws)
    if miss:
        raise TeachRefusal(f"can't anchor {', '.join(miss)} on this item")
    trial = copy.deepcopy(pmap)
    for p, v in values.items():
        trial[p]["examples"] = list(dict.fromkeys(trial[p]["examples"] + [v]))
        if trial[p]["type"] == "text":
            trial[p]["shape"] = text_shape(trial[p]["examples"])
        if trial[p]["type"] == "date":
            f = raw_date_format(raws[p][1], v)
            trial[p]["raw_formats"] = sorted(set(trial[p].get("raw_formats", [])) | ({f} if f else set()))
    r = match_profile(prof, text, trial, kind)
    if r["status"] != "ok" or r["values"] != values:
        raise TeachRefusal(f"the new layout does not reproduce the values you entered ({r['why'] or r['values']})")
    prof["from"] = {"taught": True, "item": (halt.get("item") or {}).get("ref")}
    L["profiles"].append(prof)
    cand["params"] = [trial[p["name"]] for p in cand["params"]]
    return {"kind": "item_profile", "kind_of_item": kind, "params": sorted(values)}


def teach(program: dict, halt: dict, fix_trace: Trace, *,
          gate: Optional[Callable[[dict, dict], dict]] = None) -> dict:
    """Halt report + fix demonstration -> {"status": promoted|refused|candidate, "program", "change", "gate"}."""
    if not halt or halt.get("status") != "HALTED":
        return {"status": "refused", "reason": "not a halt report", "program": None}
    cand = copy.deepcopy(program)
    try:
        if halt["reason"] == "unmatched_item":
            change = _teach_item(cand, halt, fix_trace)
        elif halt["reason"] in ("missing_control", "ambiguous_match", "unknown_dialog", "postcondition_failed"):
            change = _teach_ui(cand, halt, fix_trace)
        else:
            raise TeachRefusal(f"I don't know how to learn a fix for a {halt['reason']!r} halt")
    except TeachRefusal as exc:
        return {"status": "refused", "reason": str(exc), "program": None}
    cand["version"] = program["version"] + 1
    cand["parent"] = {"version": program["version"], "sha256": program_sha(program)}
    entry = {"version": cand["version"], "kind": "halt_teach", "at": now_iso(), "change": change,
             "halt": {"reason": halt["reason"], "op_id": halt.get("op_id"), "message": halt.get("message"),
                      "instance": halt.get("instance"), "item": (halt.get("item") or {}).get("ref")},
             "fix_trace": {"sha256": _trace_sha(fix_trace), "steps": len(fix_trace.steps), "instance": fix_trace.instance}}
    cand["provenance"].append(entry)
    if gate is None:
        return {"status": "candidate", "program": cand, "change": change, "reason": "no gate given; not promoted"}
    res = gate(cand, halt)
    entry["gate"] = {k: v for k, v in res.items() if k in ("ok", "demo", "canary", "canary_success", "repeats_halt")}
    if not res.get("ok"):
        return {"status": "refused", "reason": f"gate failed: {res}", "program": None, "candidate": cand,
                "change": change, "gate": res}
    return {"status": "promoted", "program": cand, "change": change, "gate": res}


# ============================================================== library ==

class SkillLibrary:
    """Versioned skills on disk: <root>/<skill_id>/v<N>.json + active.json (pointer)."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def save(self, program: dict, *, activate: bool = True) -> Path:
        d = self.root / program["skill_id"]
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"v{program['version']}.json"
        p.write_text(json.dumps(program, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if activate:
            (d / "active.json").write_text(json.dumps({"version": program["version"], "sha256": program_sha(program)}) + "\n",
                                           encoding="utf-8")
        return p

    def versions(self, skill_id: str) -> List[int]:
        return sorted(int(p.stem[1:]) for p in (self.root / skill_id).glob("v*.json"))

    def load(self, skill_id: str, version: Optional[int] = None) -> dict:
        d = self.root / skill_id
        if version is None:
            version = json.loads((d / "active.json").read_text())["version"]
        return json.loads((d / f"v{version}.json").read_text(encoding="utf-8"))


def render_program(program: dict) -> str:
    """Human-readable summary of an executable skill program."""
    L = [f"SKILL PROGRAM {program['skill_id']} v{program['version']}"
         + (f" (parent v{program['parent']['version']})" if program.get("parent") else "")]
    for p in program["params"]:
        L.append(f"  param {p['name']} ({p['type']}, per {p['scope']}) <- field '{p.get('field_label')}'"
                 + (f", seen near '{p.get('seen_near')}'" if p.get("seen_near") else ""))
    for part in ("setup", "finally"):
        for o in program[part]:
            L.append(f"  {part}: {_desc(o)}")
    lp = program.get("loop")
    if lp:
        L.append(f"  for each item in {lp['source']}: {len(lp['profiles'])} layout(s), unmatched -> {lp['on_unmatched']}"
                 + (", bounded agent step available" if lp.get("agent_step") else ""))
        for o in lp["body"]:
            L.append(f"    {_desc(o)}")
    for h in program["handlers"]:
        L.append(f"  handler {h['id']}: when {h['guard']['text_all']} -> click {[d['target']['name'] for d in h['do']]}")
    return "\n".join(L)
