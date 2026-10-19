"""
compiler.py — compile ONE demonstration trace into a reusable task description ("skill").

    skill = compile_trace(Trace.load("demo_trace.json"))
    text  = render_skill(skill)                     # what an agent/user reads
    ok    = evaluate_check(skill, start_text, end_home, end_tables, bindings)

What it infers, from the trace alone (no ground truth, no task-specific code):

  parameters  every value the user produced (typed text, file names, ledger
              fields, report numbers) is explained as coming from something
              the user SAW: a literal in a document / page ("From: <vendor>"),
              a reformatted date ("March 31, 2026" -> ISO), a parsed amount
              ("$2,345.67" -> 2345.67), a slug ("Northwind Credit Union" ->
              northwind-credit-union), the clock (monday_of(today)), or an
              aggregate over a table the user exported (sum(amount) by customer
              where status == paid). Values not explained are constants.
  items/loop  values grouped by the observation they came from = one item
              (one statement file, one invoice e-mail); the steps of the first
              item become the loop body; items the user looked at and skipped
              give a skip rule (e.g. invoice number already in the Bills table).
  recovery    a field retyped after a validation alert -> an on-error rule.
  invariants  start files the demo never touched stay unchanged; nothing is
              deleted; only the demo's apps are used; no destructive controls.
  check       derived from the demo's END state vs START state: files moved
              with identical content to the templated path, CSV rows appended
              (old rows preserved) whose fields follow the templates, files
              created that contain the templated lines, web table rows added.

The skill dict is the DESCRIPTIVE form (for an agent prompt / the user). The
EXECUTABLE form — multi-demo induction, layout profiles per item, guarded
branches, refusals, halt -> teach versioning — is built on top of it by
induction.py and run with zero model calls by skill_runner.py:

    res = induce_skill([trace, ...])      # == induction.induce
    res.program -> skill_runner.SkillRunner(res.program, env).run()
"""
from __future__ import annotations

import datetime as dt
import re
from collections import OrderedDict
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .common import (csv_dicts, csv_rows, find_dates, fmt_money, parse_date, parse_money, sha256_file, slug,
                     week_bounds)
from .trace import Step, Trace

SKILL_SCHEMA = "awos.skill/1"
MONEY_TOKEN = re.compile(r"(?:[$€]|EUR|USD)?\s?\d[\d,.]*\d")
DESTRUCTIVE_RE = r"delete|archive|remove|trash"


def _name(s: str) -> str:
    return "_".join(re.findall(r"[a-z0-9]+", s.lower())) or "value"


def _label_of(line: str, raw: str) -> str:
    head = line.split(raw)[0]
    m = re.search(r"([A-Za-z][A-Za-z ]{1,30}):\s*[^:]*$", head)
    if m:
        return m.group(1).strip()
    words = re.findall(r"[A-Za-z]+", head)
    return " ".join(words[-3:])


def _line_with(text: str, raw: str) -> str:
    for ln in text.splitlines():
        if raw in ln:
            return ln.strip()
    return ""


CONTEXT_RELATIONS = {
    "today": lambda d: d,
    "monday_of(today)": lambda d: week_bounds(d)[0],
    "sunday_of(today)": lambda d: week_bounds(d)[1],
    "first_of_month(today)": lambda d: d.replace(day=1),
}


# ============================================================ observations ==

def observations(trace: Trace) -> List[dict]:
    """Everything the user saw, in order: documents read and pages loaded."""
    out = []
    for s in trace.steps:
        if s.action == "read" and s.observed.get("text"):
            text = s.observed["text"]
            table = csv_dicts(text) if s.target.get("path", "").endswith(".csv") else None
            out.append({"step": s.i, "app": s.app, "text": text, "table": table, "kind": "document",
                        "path": s.target.get("path")})
        if s.after.get("page_text") is not None:
            out.append({"step": s.i, "app": s.app, "text": s.after["page_text"], "tables": s.after.get("tables", []),
                        "table": None, "kind": "page", "url_path": s.after.get("url_path")})
    return out


def _derive(value: str, obs: List[dict], ctx: dict) -> Optional[dict]:
    """Explain one produced value from the observations before it (latest first)."""
    v = str(value).strip()
    if not v:
        return None
    iso = parse_date(v, ("%Y-%m-%d",)) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) else None
    if iso and ctx.get("today"):
        today = dt.date.fromisoformat(ctx["today"])
        for rel, f in CONTEXT_RELATIONS.items():
            if f(today) == iso:
                return {"type": "date", "source": "context", "relation": rel, "format": "%Y-%m-%d"}
    for o in reversed(obs):
        text = o["text"]
        if iso:
            hits = [(raw, d) for raw, d in find_dates(text) if d == iso]
            if hits:
                raw = hits[-1][0]
                line = _line_with(text, raw)
                fmt = next((f for f in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%d %b %Y", "%m/%d/%Y",
                                        "%d.%m.%Y", "%Y/%m/%d", "%Y.%m.%d", "%d/%m/%Y")
                            if parse_date(raw, (f,)) == iso), None)
                return {"type": "date", "source": "observation", "obs_step": o["step"], "raw": raw,
                        "raw_format": fmt, "format": "%Y-%m-%d", "label": _label_of(line, raw), "line": line}
        if re.fullmatch(r"\d+\.\d{2}", v):
            for m in MONEY_TOKEN.finditer(text):
                if parse_money(m.group(0)) == Decimal(v):
                    raw = m.group(0).strip()
                    line = _line_with(text, raw)
                    return {"type": "money", "source": "observation", "obs_step": o["step"], "raw": raw,
                            "format": "0.00", "label": _label_of(line, raw), "line": line}
        if re.search(r"(?<![\w])" + re.escape(v) + r"(?![\w])", text) and len(v) >= 2:
            line = _line_with(text, v)
            return {"type": "text", "source": "observation", "obs_step": o["step"], "raw": v,
                    "label": _label_of(line, v), "line": line}
        if re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", v):
            for ln in text.splitlines():
                words = re.findall(r"[A-Za-z0-9&']+", ln)
                for n in range(1, 6):
                    for k in range(len(words) - n + 1):
                        phrase = " ".join(words[k:k + n])
                        if slug(phrase) == v:
                            return {"type": "slug", "source": "observation", "obs_step": o["step"],
                                    "raw": phrase, "transform": "lowercase, words joined by '-'",
                                    "line": ln.strip(), "label": _label_of(ln, words[k])}
    return None


# =============================================================== templating ==

def _template(value: str, bindings: Dict[str, str], dates: Dict[str, str]) -> str:
    """Replace bound values in a string by {param} placeholders (longest first)."""
    out = value
    marks = {}
    for k, (name, iso) in enumerate(sorted(dates.items(), key=lambda kv: -len(kv[1]))):
        d = dt.date.fromisoformat(iso)
        for pat, ph in ((iso, f"{{{name}}}"), (f"{d:%Y}/{d:%m}", f"{{{name}:%Y}}/{{{name}:%m}}")):
            if pat in out:
                m = f"\x00{len(marks)}\x00"
                marks[m] = ph
                out = out.replace(pat, m)
    for name, val in sorted(bindings.items(), key=lambda kv: -len(kv[1])):
        if len(val) <= 2:     # short values (counts): whole tokens only
            pat = re.compile(r"(?<![\w.])" + re.escape(val) + r"(?![\w.])")
            if val and pat.search(out):
                m = f"\x00{len(marks)}\x00"
                marks[m] = f"{{{name}}}"
                out = pat.sub(m, out)
            continue
        if val in out:
            m = f"\x00{len(marks)}\x00"
            marks[m] = f"{{{name}}}"
            out = out.replace(val, m)
    for m, ph in marks.items():
        out = out.replace(m, ph)
    return out


def render(template: str, values: Dict[str, str]) -> str:
    def sub(m):
        name, _, fmt = m.group(1).partition(":")
        v = values[name]
        if fmt:
            return dt.date.fromisoformat(v).strftime(fmt)
        return str(v)
    return re.sub(r"\{([a-z0-9_]+(?::[^}]+)?)\}", sub, template)


# ================================================================ aggregates ==

def _agg_hypotheses(table: List[dict]) -> List[dict]:
    cols = list(table[0].keys()) if table else []
    cat = [c for c in cols if 1 < len({r[c] for r in table}) <= 5]
    filters = [None] + [(c, "==", v) for c in cat for v in sorted({r[c] for r in table})] \
        + [(c, "!=", v) for c in cat for v in sorted({r[c] for r in table})]
    return [{"filter": f} for f in filters]


def _apply_filter(rows: List[dict], f) -> List[dict]:
    if not f:
        return rows
    c, op, v = f
    return [r for r in rows if (r[c] == v) == (op == "==")]


def aggregate(table: List[dict], spec: dict) -> List[List[str]]:
    """Rows produced by an aggregate spec over an observed/exported table."""
    rows = _apply_filter(table, tuple(spec["filter"]) if spec.get("filter") else None)
    groups: "OrderedDict[str, list]" = OrderedDict()
    for r in rows:
        groups.setdefault(r[spec["group_by"]], []).append(r)
    out = []
    for key in sorted(groups):
        rs = groups[key]
        row = []
        for col in spec["columns"]:
            kind = col["kind"]
            if kind == "group_key":
                row.append(key)
            elif kind == "count":
                row.append(str(len(rs)))
            elif kind == "sum":
                row.append(fmt_money(sum(Decimal(r[col["of"]]) for r in rs)))
            elif kind == "param":
                row.append("{" + col["param"] + "}")
        out.append(row)
    return out


def agg_candidates(spec: dict, rows: List[List[str]]) -> Dict[str, str]:
    """Report-level values over aggregate rows: sum_<col>, max_<col>, argmax_<col> (= the group key)."""
    out: Dict[str, str] = {}
    if not rows:
        return out
    hdr = spec["header"]
    key_j = next(k for k, c in enumerate(spec["columns"]) if c["kind"] == "group_key")
    for j, col in enumerate(spec["columns"]):
        if col["kind"] in ("count", "sum"):
            tot = sum(Decimal(r[j]) for r in rows)
            out[f"sum_{_name(hdr[j])}"] = str(int(tot)) if col["kind"] == "count" else fmt_money(tot)
            top = max(rows, key=lambda r: (Decimal(r[j]), r[key_j]))
            out[f"max_{_name(hdr[j])}"] = top[j]
            out[f"argmax_{_name(hdr[j])}"] = top[key_j]
    return out


def _infer_aggregate(header: List[str], new_rows: List[List[str]], table: List[dict],
                     globals_: Dict[str, str]) -> Optional[dict]:
    if not table:
        return None
    cols = list(table[0].keys())
    numeric = [c for c in cols if all(re.fullmatch(r"-?\d+(\.\d+)?", r[c] or "x") for r in table)]
    spec_cols: List[Optional[dict]] = [None] * len(header)
    group_col = None
    for j in range(len(header)):
        vals = [r[j] for r in new_rows]
        for name, gv in globals_.items():
            if all(v == gv for v in vals):
                spec_cols[j] = {"kind": "param", "param": name}
                break
        if spec_cols[j] is None and group_col is None:
            for c in cols:
                if len(set(vals)) == len(vals) and all(any(r[c] == v for r in table) for v in vals):
                    group_col, spec_cols[j] = c, {"kind": "group_key"}
                    break
    if group_col is None:
        return None
    key_j = spec_cols.index({"kind": "group_key"})
    for hyp in _agg_hypotheses(table):
        rows = _apply_filter(table, hyp["filter"])
        if sorted({r[group_col] for r in rows}) != sorted(r[key_j] for r in new_rows):
            continue
        trial = list(spec_cols)
        ok = True
        for j in range(len(header)):
            if trial[j] is not None:
                continue
            for cand in [{"kind": "count"}] + [{"kind": "sum", "of": c} for c in numeric]:
                spec = {"group_by": group_col, "filter": hyp["filter"], "columns": [
                    cand if k == j else {"kind": "group_key"} for k in range(len(header))]}
                got = {r[key_j]: r for r in aggregate(table, spec)}
                if all(got.get(row[key_j], [None] * len(header))[j] == row[j] for row in new_rows):
                    trial[j] = cand
                    break
            else:
                ok = False
                break
        if ok:
            return {"group_by": group_col, "filter": list(hyp["filter"]) if hyp["filter"] else None,
                    "columns": trial, "header": header}
    return None


# ================================================================== compile ==

def _atoms(s: Step, headers: Dict[str, List[str]]) -> List[Tuple[str, str]]:
    if s.action == "type" and isinstance(s.value, str):
        return [(_name(s.target.get("name", "value")), s.value)]
    if s.action == "append_rows" and isinstance(s.value, list) and len(s.value) == 1:
        hdr = headers.get(s.target["path"], [])
        return [(_name(hdr[j]) if j < len(hdr) else f"col{j}", v) for j, v in enumerate(s.value[0])]
    return []


def _obs_kind(o: dict) -> tuple:
    """Two observations are the same kind of thing: same app page (ids masked) or same folder."""
    if o["kind"] == "page":
        return (o["app"], "page", re.sub(r"\d+", "#", (o.get("url_path") or "").split("?")[0]))
    return (o["app"], "document", str(Path(o.get("path") or "").parent))


def compile_trace(trace: Trace) -> dict:
    obs = observations(trace)
    ctx = trace.context
    headers = {rel: (csv_rows(t) or [[]])[0] for rel, t in trace.start.get("text", {}).items() if rel.endswith(".csv")}
    params: "OrderedDict[str, dict]" = OrderedDict()
    item_values: "OrderedDict[int, Dict[str, str]]" = OrderedDict()   # obs step -> {param: value}
    globals_: Dict[str, str] = {}
    step_item: Dict[int, int] = {}
    step_bind: Dict[int, List[Tuple[str, str, dict]]] = {}

    def add_param(name: str, d: dict, value: str, item: Optional[int]) -> str:
        # reuse a global param that already has this exact value and source
        if item is None:
            for pn, pv in globals_.items():
                if pv == value and params[pn]["source"].get("relation") == d.get("relation"):
                    return pn
        p = params.get(name)
        spec = {"name": name, "type": d["type"], "scope": "item" if item is not None else "global",
                "source": {k: v for k, v in d.items() if k not in ("type", "obs_step", "line")}, "examples": []}
        if p is None:
            params[name] = p = spec
        elif item is not None and name in item_values.get(item, {}):
            # retyped for the same item (e.g. after a validation error): the last value wins
            p["examples"] = [e for e in p["examples"] if e.get("item") != item]
            if not p["examples"]:
                p.update({k: spec[k] for k in ("type", "source")})
        p["examples"].append({"value": value, "raw": d.get("raw", value), "line": d.get("line", ""), "item": item})
        if item is None:
            globals_[name] = value
        else:
            item_values.setdefault(item, {})[name] = value
        return name

    # --- 1. explain every produced atom
    for s in trace.steps:
        prior = [o for o in obs if o["step"] < s.i or (o["step"] == s.i and s.action == "read")]
        for slot, val in _atoms(s, headers):
            d = _derive(val, prior, ctx)
            if d is None:
                continue
            item = d.get("obs_step") if d["source"] == "observation" else None
            pn = add_param(slot, d, val, item)
            step_bind.setdefault(s.i, []).append((slot, pn, d))
            if item is not None:
                step_item[s.i] = item

    # --- 2. items: observations that explain values; skipped look-alikes
    item_steps = sorted(item_values)
    item_kinds = {_obs_kind(o) for o in obs if o["step"] in item_steps}
    skipped = [o["step"] for o in obs if o["step"] not in item_steps and o["step"] > (item_steps[0] if item_steps else 0)
               and _obs_kind(o) in item_kinds]
    boundaries = sorted(item_steps + skipped)

    def block_of(i: int) -> Optional[int]:
        cur = None
        for b in boundaries:
            if b <= i:
                cur = b
        return cur

    # --- 3. templated procedure
    first_item = item_steps[0] if item_steps else None
    second_boundary = next((b for b in boundaries if first_item and b > first_item), None)
    vals_global = dict(globals_)
    proc_setup, proc_body, proc_tail = [], [], []
    recovery = []
    last_tables: Dict[str, list] = {}
    for s in trace.steps:
        blk = block_of(s.i)
        item_vals = item_values.get(blk, {}) if blk in item_values else {}
        dates = {k: v for k, v in {**item_vals, **vals_global}.items()
                 if params.get(k, {}).get("type") == "date"}
        plain = {k: v for k, v in {**item_vals, **vals_global}.items() if k not in dates}
        g = {"app": s.app, "action": s.action, "step": s.i}
        if s.action in ("click", "download"):
            name = s.target.get("name", "")
            in_table = None
            for t in last_tables.get(s.app, []):
                if any(name in row for row in t["rows"]):
                    in_table = re.sub(r"\s*\(.*\)$", "", t["caption"])
            if in_table and s.i in boundaries:
                g["target"] = {"role": s.target.get("role"), "select": f"each row of table '{in_table}' (one item)"}
            else:
                g["target"] = {"role": s.target.get("role"), "name": name}
            for f in s.files_touched:
                if f["op"] == "created":
                    g["saves"] = _template(f["path"], plain, dates)
        elif s.action == "type":
            g["target"] = {"role": s.target.get("role"), "name": s.target.get("name")}
            if isinstance(s.value, dict):
                g["value"] = "<secret: " + s.value.get("secret", "") + " from the user's keychain/config>"
            else:
                bound = [b for b in step_bind.get(s.i, [])]
                g["value"] = "{" + bound[0][1] + "}" if bound else s.value
            prev = [x for x in trace.steps if x.i < s.i and x.app == s.app and x.action == "type"
                    and x.target.get("name") == s.target.get("name") and block_of(x.i) == blk]
            clicks = [x for x in trace.steps if prev and prev[-1].i < x.i < s.i and x.action == "click"
                      and x.after.get("alerts")]
            if prev and clicks:
                recovery.append({"field": s.target.get("name"), "after_click": clicks[-1].target.get("name"),
                                 "when_alert": [a for a in clicks[-1].after["alerts"]
                                                if s.target.get("name", "").lower() in a.lower()]
                                 or clicks[-1].after["alerts"],
                                 "first_value": prev[-1].value, "fixed_value": g["value"],
                                 "lesson": f"the form rejected {prev[-1].value!r}; retyped as {g['value']} "
                                           f"(demo value {s.value!r}) and saved again"})
                g["recovery"] = True
        elif s.action == "move":
            g["from"] = _template(s.target["from"], plain, dates)
            src = s.target["from"]
            ext = Path(src).suffix.lstrip(".")
            to = _template(s.target["to"], plain, dates)
            if ext and to.endswith("." + ext):
                to = to[: -len(ext)] + "{src_ext}"
            g["to"] = to
            if s.i in step_item or blk in item_values:
                g["from"] = "{src_path}"
                item_values.setdefault(blk, {})["src_path"] = src
                item_values[blk]["src_ext"] = ext
                item_values[blk]["src_sha256"] = next((f.get("sha256") for f in s.files_touched if f["op"] == "moved"), "")
        elif s.action == "read":
            g["target"] = {"path": _template(s.target["path"], plain, dates)}
            if s.i in boundaries:
                folder = str(Path(s.target["path"]).parent)
                g["target"] = {"select": f"each file in {folder}/ that is an item (one item)"}
        elif s.action == "append_rows":
            g["target"] = {"path": s.target["path"]}
            if s.i in step_bind:
                g["row"] = ["{" + pn + "}" for _, pn, _ in step_bind[s.i]] if len(step_bind[s.i]) == len(s.value[0]) \
                    else s.value[0]
        elif s.action == "write_file":
            g["target"] = {"path": _template(s.target["path"], plain, dates)}
        elif s.action in ("open_url",):
            g["target"] = {"url_path": s.target.get("url_path")}
        elif s.action == "switch_app":
            pass
        if s.after.get("tables"):
            last_tables[s.app] = s.after["tables"]
        if first_item is None or s.i < first_item:
            proc_setup.append(g)
        elif second_boundary is None or s.i < second_boundary:
            proc_body.append(g)
        elif blk not in item_values and blk not in skipped:
            proc_tail.append(g)

    # --- 4. row templates for single-row appends now that src_* are known
    for s in trace.steps:
        if s.action == "append_rows" and s.i in step_bind and s.i in step_item:
            blk = block_of(s.i)
            iv = item_values.get(blk, {})
            dates = {k: v for k, v in iv.items() if params.get(k, {}).get("type") == "date"}
            plain = {k: v for k, v in iv.items() if k not in dates and not k.startswith("src_")}
            for g in proc_body:
                if g["action"] == "append_rows" and g.get("row"):
                    g["row"] = [_template(v, plain, dates) if not v.startswith("{") else v for v in g["row"]]
    # unexplained ledger fields (e.g. a relative file path) -> templates from the item's values
    for s in trace.steps:
        if s.action == "append_rows" and len(s.value) == 1 and s.i in step_item:
            blk = block_of(s.i)
            iv = item_values.get(blk, {})
            dates = {k: v for k, v in iv.items() if params.get(k, {}).get("type") == "date"}
            plain = {k: v for k, v in iv.items() if k not in dates and not k.startswith("src_")}
            row_t = []
            hdr = headers.get(s.target["path"], [])
            for j, v in enumerate(s.value[0]):
                pn = _name(hdr[j]) if j < len(hdr) else f"col{j}"
                if pn in iv and iv[pn] == v:
                    row_t.append("{" + pn + "}")
                else:
                    t = _template(v, plain, dates)
                    if iv.get("src_ext") and t.endswith("." + iv["src_ext"]):
                        t = t[: -len(iv["src_ext"])] + "{src_ext}"
                    row_t.append(t)
            for g in proc_body:
                if g["action"] == "append_rows" and g["target"]["path"] == s.target["path"]:
                    g["row"] = row_t
            break

    # --- 5. aggregate appends (multi-row) and derived reports
    aggregates = []
    for s in trace.steps:
        if s.action == "append_rows" and len(s.value) > 1:
            table = next((o["table"] for o in reversed(obs) if o["step"] < s.i and o.get("table")), None)
            hdr = headers.get(s.target["path"], [])
            spec = _infer_aggregate(hdr, s.value, table or [], globals_)
            if spec:
                src_obs = next(o for o in reversed(obs) if o["step"] < s.i and o.get("table"))
                spec.update({"path": s.target["path"], "source_document": src_obs.get("path"),
                             "source_template": _template(src_obs.get("path") or "", {}, globals_)})
                aggregates.append(spec)
                for g in proc_body + proc_setup:
                    if g["action"] == "append_rows" and g["target"]["path"] == s.target["path"]:
                        g["rows"] = {"aggregate": _describe_agg(spec)}
    derived_lines = []
    for s in trace.steps:
        if s.action == "write_file" and isinstance(s.value, str):
            cands = dict(globals_)
            for spec in aggregates:
                rows = next(st for st in trace.steps if st.action == "append_rows"
                            and st.target["path"] == spec["path"]).value
                cands.update(agg_candidates(spec, rows))
            dates = {k: v for k, v in cands.items() if params.get(k, {}).get("type") == "date"}
            plain = {k: v for k, v in cands.items() if k not in dates}
            lines = [_template(ln, plain, dates) for ln in s.value.splitlines() if ln.strip()]
            derived_lines.append({"path": _template(s.target["path"], {}, dates), "lines": lines})

    # --- 6. skip rules
    skip_rules = []
    for k in skipped:
        o = next(x for x in obs if x["step"] == k)
        tokens = set(re.findall(r"[A-Z]{2,}-\d+(?:-\d+)?", o["text"]))
        for other in obs:
            for t in other.get("tables", []) or []:
                for ci, colname in enumerate(t["header"]):
                    if any(row[ci] in tokens for row in t["rows"] if ci < len(row)):
                        pn = next((p for p in params if p == _name(colname)), _name(colname))
                        rule = {"skip_item_if": f"{{{pn}}} is already in table '{t['caption']}' column "
                                                f"'{colname}' of app '{other['app']}'",
                                "param": pn, "app": other["app"], "table": t["caption"], "column": colname,
                                "evidence": f"demo step {k}: the user opened this item and did not enter it"}
                        if rule not in skip_rules:
                            skip_rules.append(rule)

    skill = {
        "schema": SKILL_SCHEMA, "task": trace.task, "compiled_from": {"instance": trace.instance,
                                                                     "steps": len(trace.steps)},
        "note": trace.note, "apps": sorted({s.app for s in trace.steps if s.app}),
        "context_used": sorted({p["source"]["relation"] for p in params.values() if p["source"].get("relation")}),
        "parameters": list(params.values()),
        "items": {"count_in_demo": len(item_steps), "skipped_in_demo": len(skipped),
                  "unselected_examples": _unselected(trace, item_steps),
                  "steps": list(item_steps), "skipped_steps": list(skipped)},
        "procedure": {"setup": proc_setup, "for_each_item": proc_body, "finally": proc_tail},
        "recovery": recovery, "skip_rules": skip_rules, "aggregates": aggregates,
        "invariants": _invariants(trace),
        "check": [],
        "bindings": {"global": dict(globals_), "items": [item_values[k] for k in item_steps]},
    }
    skill["check"] = _derive_check(trace, skill, derived_lines)
    return skill


def _describe_agg(spec: dict) -> str:
    parts = []
    for h, c in zip(spec["header"], spec["columns"]):
        if c["kind"] == "group_key":
            parts.append(f"{h}={spec['group_by']}")
        elif c["kind"] == "count":
            parts.append(f"{h}=count(rows)")
        elif c["kind"] == "sum":
            parts.append(f"{h}=sum({c['of']})")
        else:
            parts.append(f"{h}={{{c['param']}}}")
    f = spec.get("filter")
    where = f" where {f[0]} {f[1]} '{f[2]}'" if f else ""
    return f"one row per {spec['group_by']}{where}: " + ", ".join(parts)


def _unselected(trace: Trace, item_steps: List[int]) -> List[str]:
    read_dirs = {str(Path(s.target["path"]).parent) for s in trace.steps if s.action == "read" and s.i in item_steps}
    touched = {s.target.get("path") or s.target.get("from") for s in trace.steps}
    return sorted(rel for rel in trace.start.get("files", {})
                  if str(Path(rel).parent) in read_dirs and rel not in touched)


def _invariants(trace: Trace) -> List[dict]:
    touched = set()
    for s in trace.steps:
        for f in s.files_touched:
            if f["op"] != "read":
                touched.add(f.get("from") or f["path"])
    keep = sorted(rel for rel in trace.start.get("files", {}) if rel not in touched)
    clicked = [s.target.get("name", "") for s in trace.steps if s.action in ("click", "download")]
    return [
        {"kind": "no_deletions", "why": "no start file disappeared in the demo (moves keep content)"},
        {"kind": "untouched_stay_unchanged", "examples": keep[:20],
         "why": "files the demo never wrote keep their content (unless they are this run's items)"},
        {"kind": "apps_only", "apps": sorted({s.app for s in trace.steps if s.app})},
        {"kind": "no_destructive_controls", "pattern": DESTRUCTIVE_RE,
         "why": "the demo never pressed " + "/".join(sorted({n for n in clicked if re.search(DESTRUCTIVE_RE, n, re.I)}))
                if any(re.search(DESTRUCTIVE_RE, n, re.I) for n in clicked) else "the demo pressed no destructive control"},
    ]


def _derive_check(trace: Trace, skill: dict, derived_lines: List[dict]) -> List[dict]:
    """Success check derived from the demo's END state vs its START state."""
    checks = []
    start, end = trace.start.get("files", {}), trace.end.get("files", {})
    start_text, end_text = trace.start.get("text", {}), trace.end.get("text", {})
    body = skill["procedure"]["for_each_item"] + skill["procedure"]["setup"]
    # moved files: start sha now at a new path, old path gone
    moved = [(rel, sha) for rel, sha in start.items() if rel not in end and sha in end.values()]
    mv = next((g for g in body if g["action"] == "move"), None)
    if moved and mv:
        checks.append({"kind": "file_moved", "per": "item", "from": "{src_path}", "to": mv["to"],
                       "content": "identical (same sha256)", "evidence": f"{len(moved)} file(s) moved in the demo"})
    # appended CSV rows
    for rel, old in start_text.items():
        new = end_text.get(rel)
        if not rel.endswith(".csv") or new is None or new == old:
            continue
        o, n = csv_rows(old), csv_rows(new)
        if n[:len(o)] != o:
            checks.append({"kind": "csv_rewritten", "path": rel})
            continue
        agg = next((a for a in skill["aggregates"] if a["path"] == rel), None)
        row = next((g.get("row") for g in body if g["action"] == "append_rows" and g["target"]["path"] == rel), None)
        if agg:
            checks.append({"kind": "csv_rows_appended", "path": rel, "preserve_existing": True,
                           "rows": "aggregate", "aggregate": agg, "evidence": f"{len(n) - len(o)} row(s) appended"})
        elif row:
            checks.append({"kind": "csv_rows_appended", "path": rel, "preserve_existing": True, "per": "item",
                           "row": row, "evidence": f"{len(n) - len(o)} row(s) appended"})
    # created files (non-download) with derived lines
    for d in derived_lines:
        checks.append({"kind": "file_created", "path": d["path"], "contains_lines": d["lines"]})
    for g in body:
        if g.get("saves"):
            checks.append({"kind": "file_downloaded", "path": g["saves"]})
    # web tables: rows added between first and last observation of the same table
    first: Dict[tuple, dict] = {}
    last: Dict[tuple, dict] = {}
    for s in trace.steps:
        for t in s.after.get("tables", []) or []:
            key = (s.app, re.sub(r"\s*\(.*\)$", "", t["caption"]))
            first.setdefault(key, t)
            last[key] = t
    for app, page in (trace.end.get("web") or {}).items():
        for t in page.get("tables", []) or []:
            last[(app, re.sub(r"\s*\(.*\)$", "", t["caption"]))] = t
    items = skill["bindings"]["items"]
    for key, t0 in first.items():
        t1 = last[key]
        added = [r for r in t1["rows"] if r not in t0["rows"]]
        if not added or key[0] not in skill["apps"] or len(added) != len(items):
            continue
        cols = {}
        for ci, colname in enumerate(t1["header"]):
            for pn in items[0]:
                if all(ci < len(r) and any(r[ci] == iv.get(pn) for iv in items) for r in added):
                    cols[colname] = "{" + pn + "}"
                    break
        if cols:
            checks.append({"kind": "web_rows_added", "per": "item", "app": key[0], "table": key[1],
                           "columns": cols, "preserve_existing": True})
    checks.append({"kind": "no_deletions"})
    return checks


# ================================================================= evaluate ==

def evaluate_check(skill: dict, start_text: Dict[str, str], home: Path,
                   end_tables: Optional[Dict[str, List[dict]]] = None,
                   bindings: Optional[dict] = None, start_files: Optional[Dict[str, str]] = None) -> List[dict]:
    """Evaluate the derived check on an end state with given bindings (default: the demo's)."""
    home = Path(home)
    b = bindings or skill["bindings"]
    g = b["global"]
    out = []
    derived: Dict[str, str] = {}

    def res(check, ok, why=""):
        out.append({"kind": check["kind"], "passed": bool(ok), "why": why})

    for c in skill["check"]:
        k = c["kind"]
        if k == "file_moved":
            bad = []
            for iv in b["items"]:
                v = {**g, **iv}
                to = home / render(c["to"], v)
                if (home / iv["src_path"]).exists() or not to.exists() or sha256_file(to) != iv["src_sha256"]:
                    bad.append(iv["src_path"])
            res(c, not bad, f"not moved: {bad}" if bad else "")
        elif k == "csv_rows_appended":
            p = home / c["path"]
            old = csv_rows(start_text.get(c["path"], ""))
            new = csv_rows(p.read_text(encoding="utf-8")) if p.exists() else []
            if c.get("rows") == "aggregate":
                agg = c["aggregate"]
                src = home / render(agg["source_template"], g) if agg.get("source_template") else None
                table = csv_dicts(src.read_text(encoding="utf-8")) if src and src.exists() else []
                want = [[render(x, g) for x in r] for r in aggregate(table, agg)] if table else None
                derived.update(agg_candidates(agg, want or []))
            else:
                want = [[render(x, {**g, **iv}) for x in c["row"]] for iv in b["items"]]
            ok = (want is not None and new[:len(old)] == old
                  and sorted(new[len(old):]) == sorted(want))
            res(c, ok, "" if ok else f"appended {new[len(old):]} want {want}")
        elif k == "file_created":
            p = home / render(c["path"], g)
            vals = {**g, **derived}
            ok = p.exists()
            if ok:
                text = p.read_text(encoding="utf-8")
                for ln in c["contains_lines"]:
                    names = re.findall(r"\{([a-z0-9_]+)", ln)
                    ok = ok and all(n in vals for n in names) and render(ln, vals) in text
            res(c, ok)
        elif k == "file_downloaded":
            res(c, (home / render(c["path"], g)).exists())
        elif k == "web_rows_added":
            rows = (end_tables or {}).get(c["app"], {}).get(c["table"])
            ok = rows is not None
            if ok:
                for iv in b["items"]:
                    want = {col: render(t, {**g, **iv}) for col, t in c["columns"].items()}
                    ok = ok and any(all(r.get(col) == v for col, v in want.items()) for r in rows)
            res(c, ok)
        elif k == "no_deletions":
            if start_files is None:
                res(c, True, "no start manifest given")
            else:
                from .common import manifest
                endm = manifest(home)
                kept = set(endm.values())
                lost = [r for r, sha in start_files.items() if r not in endm and sha not in kept]
                res(c, not lost, f"lost {lost}" if lost else "")
        else:
            res(c, False, "unknown check")
    return out


def tables_from_page(page_summary: dict) -> Dict[str, List[dict]]:
    out = {}
    for t in page_summary.get("tables", []) or []:
        cap = re.sub(r"\s*\(.*\)$", "", t["caption"])
        out[cap] = [dict(zip(t["header"], r)) for r in t["rows"]]
    return out


# =================================================================== render ==

def render_skill(skill: dict) -> str:
    """A plain-text version of the skill for an agent prompt or the user."""
    L = [f"SKILL for {skill['task']} (learned from one demonstration, {skill['compiled_from']['steps']} steps)",
         f"User note: {skill['note']}", f"Apps: {', '.join(skill['apps'])}"]
    if skill["context_used"]:
        L.append(f"Uses the clock: {', '.join(skill['context_used'])}")
    L.append("Parameters (change every run):")
    for p in skill["parameters"]:
        s = p["source"]
        src = (f"from context {s['relation']}" if s.get("source") == "context"
               else f"from what you see near '{s.get('label', '')}'"
               + (", seen as " + " / ".join(repr(r) for r in dict.fromkeys(e["raw"] for e in p["examples"]))
                  if s.get("raw") else "")
               + (f", reformatted from {s['raw_format']} to {s['format']}"
                  if s.get("raw_format") and s["raw_format"] != s.get("format") else "")
               + (", written as 0.00 (no currency sign or separators)" if p["type"] == "money" else "")
               + (f", {s['transform']}" if s.get("transform") else ""))
        L.append(f"  - {p['name']} ({p['type']}, per {p['scope']}): {src}; demo: "
                 + ", ".join(repr(e["value"]) for e in p["examples"][:3]))
    for part in ("setup", "for_each_item", "finally"):
        steps = skill["procedure"][part]
        if not steps:
            continue
        L.append(f"Procedure / {part}:")
        for g in steps:
            if g["action"] == "switch_app":
                L.append(f"  - switch to {g['app']}")
                continue
            bits = {k: v for k, v in g.items() if k not in ("app", "action", "step")}
            L.append(f"  - [{g['app']}] {g['action']} {bits}")
    for r in skill["recovery"]:
        L.append(f"On error: after '{r['after_click']}', if alert {r['when_alert']!r}: {r['lesson']}")
    for r in skill["skip_rules"]:
        L.append(f"Skip: {r['skip_item_if']} ({r['evidence']})")
    for a in skill["aggregates"]:
        L.append(f"Compute for {a['path']}: {_describe_agg(a)}")
    L.append("Never: " + "; ".join(i["kind"] for i in skill["invariants"]))
    L.append("Done when: " + "; ".join(c["kind"] + (f" {c.get('path') or c.get('to') or c.get('table', '')}")
                                        for c in skill["check"]))
    return "\n".join(L)


# ============================================================ integration ==

def induce_skill(traces, **kw):
    """Executable, versioned skill program from one or more demos (see induction.induce)."""
    from .induction import induce       # late import: induction builds on this module
    return induce(list(traces), **kw)
