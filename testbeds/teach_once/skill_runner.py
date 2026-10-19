"""
skill_runner.py — execute an induced skill program (induction.py) deterministically.

    res = SkillRunner(program, env).run()
    res["status"]       VERIFIED | COMPLETED_UNVERIFIED | HALTED
    res["model_calls"]  0 on a healthy run (only an explicit bounded agent step counts)
    res["halt"]         structured halt report when HALTED (feed it to induction.teach)

Healthy runs make ZERO model calls. Every step:
  * resolves its target through a pluggable grounding function
    (``ground(observation, target, exclude=..., fuzzy=...) -> Grounding``). The
    default, ``ground_role_name``, is a role+name ladder over the testbed's
    stdlib accessibility browser: exact name -> taught alias -> normalized name ->
    unique token overlap (never a destructive control, never another step's
    target). Two equally good candidates = AMBIGUOUS. WP6 replaces this with the
    AX grounding ladder (same signature).
  * checks its postcondition (expected page pattern, no validation alert, the
    file really moved / the rows really appended / the download really arrived).
  * HALTS on an unexpected state — unknown dialog, missing control, ambiguous
    match, failed postcondition, an item no learned layout matches, an ambiguous
    value — with a structured halt report. It never guesses. Learned handlers
    (from halt -> teach) may fire first when their guard matches the screen.
Effects are idempotent on resume: rows already appended, files already filed and
reports already written identically are not done twice; conflicts halt.

The testbed pieces live at the bottom: TestbedEnv (browser + file actor on a
seeded instance), DialogInjectingEnv (fault injection: an unexpected modal),
testbed_gate (re-gate a taught version on the demo + a canary instance),
scripted_fix (the human stand-in that demonstrates a fix) and a CLI:

    python -m testbeds.teach_once.skill_runner --run-dir /tmp/wp5 [--strict]
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import DEMO_INSTANCE, HELD_OUT, TASKS
from .browser import Browser, ElementNotFound, Page, ax_element
from .common import csv_dicts, csv_rows, fmt_money, manifest, parse_money, read_json, sha256_file, write_json
from .compiler import CONTEXT_RELATIONS, aggregate, agg_candidates, evaluate_check, render
from .demonstrate import FileActor
from .induction import (ROLE, SkillLibrary, fuzzy_pick, induce, line_hash, mask_path, match_profile, norm_name,
                        normalize_value, render_program, teach)
from .trace import Recorder, Trace, text_snapshot

NEXT_PAGE_RE = re.compile(r"^(next( page)?|older|›|»)$", re.I)


class Halt(Exception):
    def __init__(self, report: dict):
        super().__init__(report.get("message", "halted"))
        self.report = report


# ============================================================ grounding ==

@dataclass
class Grounding:
    status: str                       # ok | missing | ambiguous
    element: Optional[dict] = None
    rung: str = ""
    candidates: List[str] = field(default_factory=list)


def ground_role_name(page: Optional[Page], target: dict, *, exclude=(), fuzzy: bool = True) -> Grounding:
    """Role+name ladder on the stdlib accessibility browser's page model. Refuses rather than picks."""
    if page is None:
        return Grounding("missing", rung="no screen")
    role = ROLE.get(target.get("role"), target.get("role"))
    els = [e for e in page.elements if e["role"] == role]
    name = target.get("name") or ""
    aliases = list(target.get("aliases", []))
    rungs = (("exact", lambda e: e.get("name") == name),
             ("alias", lambda e: e.get("name") in aliases),
             ("normalized", lambda e: bool(norm_name(e.get("name"))) and
              norm_name(e.get("name")) in {norm_name(n) for n in [name] + aliases}))
    for rung, pred in rungs:
        hits = [e for e in els if pred(e)]
        if len(hits) == 1:
            return Grounding("ok", hits[0], rung)
        if len(hits) > 1:
            return Grounding("ambiguous", None, rung, [e.get("name", "") for e in hits])
    if fuzzy and name:
        st, idx = fuzzy_pick(name, [e.get("name", "") for e in els], exclude=exclude)
        if st == "ok":
            return Grounding("ok", els[idx[0]], "fuzzy", [els[idx[0]].get("name", "")])
        if st == "ambiguous":
            return Grounding("ambiguous", None, "fuzzy", [els[i].get("name", "") for i in idx])
    return Grounding("missing", None, "", [e.get("name", "") for e in els][:40])


def _page_lines(page: Optional[Page]) -> List[str]:
    if page is None:
        return []
    out = list(page.headings) + [page.title] + list(page.text_lines) + list(page.alerts)
    return [ln.strip() for ln in out if ln and ln.strip()]


# =============================================================== runner ==

class SkillRunner:
    def __init__(self, program: dict, env, *, ground: Callable = ground_role_name,
                 agent: Optional[Callable[[dict], dict]] = None, inputs: Optional[Dict[str, str]] = None,
                 fuzzy: bool = True, max_handler_firings: int = 3, max_items: int = 500):
        self.p, self.env, self.ground, self.agent = program, env, ground, agent
        self.inputs = dict(inputs or {})
        self.fuzzy, self.max_fire, self.max_items = fuzzy, max_handler_firings, max_items
        self.params = {p["name"]: p for p in program["params"]}
        self.model_calls = 0
        self.log: List[dict] = []
        self.healed: List[dict] = []
        self.fired: List[dict] = []
        self.items: List[dict] = []
        self.skipped: List[dict] = []
        self.notes: List[str] = []
        self.completed: List[str] = []
        self.globals: Dict[str, str] = {}
        self.derived: Dict[str, str] = {}
        self.tables: Dict[tuple, dict] = {}
        self.last_download: Optional[str] = None
        self.last_text: Optional[str] = None
        self.last_table: Optional[list] = None
        self._phase, self._item = "setup", None
        self._fires_this_op = 0

    # ---------------------------------------------------------------- run --
    def run(self) -> dict:
        t0 = time.monotonic()
        start_text = text_snapshot(self.env.home)
        start_files = manifest(self.env.home)
        halt, check = None, None
        try:
            self._bind_globals()
            for op in self.p["setup"]:
                self._exec(op, dict(self.globals))
            if self.p.get("loop"):
                self._phase = "item"
                self._run_loop()
                self._item = None
            self._phase = "finally"
            for op in self.p["finally"]:
                self._exec(op, dict(self.globals))
            check = self._self_check(start_text, start_files)
            status = "VERIFIED" if check["ok"] else "COMPLETED_UNVERIFIED"
        except Halt as h:
            status, halt = "HALTED", h.report
        return {"status": status, "halt": halt, "model_calls": self.model_calls,
                "skill": {"task": self.p["task"], "version": self.p["version"]},
                "items": [{k: v for k, v in it.items() if k != "src_sha256"} for it in self.items],
                "skipped": self.skipped, "healed": self.healed, "handlers_fired": self.fired,
                "check": check, "notes": self.notes, "steps": self.log, "completed_ops": list(self.completed),
                "actions": self.env.actions(), "seconds": round(time.monotonic() - t0, 3)}

    # ------------------------------------------------------------- values --
    def _bind_globals(self) -> None:
        import datetime as dt
        today = self.env.context.get("today")
        for g in self.p["globals"]:
            if not today:
                self._halt("missing_context", None, message="the skill needs today's date")
            self.globals[g["name"]] = CONTEXT_RELATIONS[g["relation"]](dt.date.fromisoformat(today)).isoformat()
        for p in self.p["params"]:
            if p["scope"] == "input":
                if p["name"] not in self.inputs:
                    self._halt("missing_input", None, message=f"run input {p['name']!r} ({p.get('field_label')}) not given")
                self.globals[p["name"]] = self.inputs[p["name"]]

    def _value(self, op: dict, vals: dict) -> str:
        v = op.get("value")
        if isinstance(v, dict) and "secret" in v:
            s = self.env.secret(v["secret"])
            if s is None:
                self._halt("missing_secret", op, message=f"no secret for {v['secret']!r} in the keychain/config")
            return s
        names = re.findall(r"\{([a-z0-9_]+)", str(v))
        secret = [n for n in names if self.params.get(n, {}).get("secret")]
        if secret:                    # a parameter the user marked secret: resolved at the sink, never logged
            s = self.env.secret(self.params[secret[0]].get("field_label") or secret[0])
            if s is None:
                self._halt("missing_secret", op, message=f"no secret for {secret[0]!r}")
            return s
        return render(str(v), vals)

    # ---------------------------------------------------------------- halt --
    def _new_text(self, app: str, page: Optional[Page]) -> List[str]:
        seen = set(self.p.get("seen_text", {}).get(app, []))
        out = []
        for ln in _page_lines(page):
            if line_hash(ln) not in seen and ln not in out:
                out.append(ln)
        return out[:8]

    def _observed(self, app: Optional[str], page: Optional[Page], text: Optional[str] = None) -> dict:
        if page is None:
            return {"app": app, "text": (text or "")[:6000]}
        return {"app": app, "url_path": page.path, "title": page.title, "headings": list(page.headings),
                "alerts": list(page.alerts), "text": (text if text is not None else page.text())[:6000],
                "controls": [{"role": e["role"], "name": e.get("name", "")} for e in page.elements
                             if e["role"] in ("button", "link", "textbox")][:60]}

    def _halt(self, reason: str, op: Optional[dict], *, page: Optional[Page] = None, text: Optional[str] = None,
              app: Optional[str] = None, message: str = "", **extra) -> None:
        app = app or (op or {}).get("app")
        new = self._new_text(app, page) if page is not None else []
        rep = {"status": "HALTED", "reason": reason, "skill": {"task": self.p["task"], "version": self.p["version"]},
               "op_id": (op or {}).get("id"), "op": {k: v for k, v in (op or {}).items() if k != "value"} or None,
               "phase": self._phase, "item": copy.deepcopy(self._item), "observed": self._observed(app, page, text),
               "new_text": new, "completed": list(self.completed), "message": message or reason,
               "ask": ASK.get(reason, "Show me what to do here (a short fix demonstration), or record another example."),
               "context": dict(self.env.context)}
        rep.update(extra)
        raise Halt(rep)

    # ------------------------------------------------------------ handlers --
    def _fire_handlers(self, app: str, page: Optional[Page]) -> bool:
        if page is None or self._fires_this_op >= self.max_fire:
            return False
        lines = set(_page_lines(page))
        for h in self.p.get("handlers", []):
            if h["app"] != app or not all(t in lines for t in h["guard"].get("text_all", [])):
                continue
            ctrl = h["guard"].get("control")
            if ctrl and self.ground(page, ctrl, fuzzy=False).status != "ok":
                continue
            for d in h["do"]:
                g = self.ground(self.env.page(app), d["target"], fuzzy=False)
                if g.status != "ok":
                    return False
                self.env.click(app, g.element)
                self._observe(app)
            self._fires_this_op += 1
            self.fired.append({"handler": h["id"], "op": (self._cur or {}).get("id"), "item": (self._item or {}).get("ref")})
            return True
        return False

    # ------------------------------------------------------------ resolve --
    def _exclude(self, op: dict) -> List[str]:
        out = []
        for o in self._all_ops():
            if o is op or o["app"] != op["app"]:
                continue
            t = o.get("target") or {}
            if t.get("role") == (op.get("target") or {}).get("role") and t.get("name"):
                out += [t["name"]] + list(t.get("aliases", []))
        return out

    def _all_ops(self) -> List[dict]:
        return list(self.p["setup"]) + list((self.p.get("loop") or {}).get("body", [])) + list(self.p["finally"])

    def _resolve(self, op: dict) -> Optional[dict]:
        app = op["app"]
        g = Grounding("missing")
        page = None
        for _ in range(self.max_fire + 1):
            page = self.env.page(app)
            g = self.ground(page, op["target"], exclude=self._exclude(op), fuzzy=self.fuzzy)
            if g.status == "ok":
                if g.rung != "exact":
                    self.healed.append({"op": op["id"], "target": op["target"].get("name"), "rung": g.rung,
                                        "used": g.element.get("name"), "item": (self._item or {}).get("ref")})
                return g.element
            if not self._fire_handlers(app, page):
                break
        if op.get("optional"):
            return None
        new = self._new_text(app, page)
        heads = [h for h in (page.headings if page else []) if h in new]
        reason = "ambiguous_match" if g.status == "ambiguous" else ("unknown_dialog" if heads else "missing_control")
        intended = None
        if op["action"] == "type" and not isinstance(op.get("value"), dict):
            try:
                intended = render(str(op.get("value")), {**self.globals, **(self._vals or {})})
            except KeyError:
                intended = None
        self._halt(reason, op, page=page, candidates=g.candidates, intended_value=intended,
                   expected={"target": op["target"]},
                   message=(f"{op['action']} {op['target'].get('name')!r}: " +
                            {"ambiguous_match": f"{len(g.candidates)} equally good matches {g.candidates}",
                             "unknown_dialog": f"an unexpected screen is showing ({heads[0] if heads else ''})",
                             "missing_control": "control not found on the screen"}[reason]))

    def _post_ok(self, post: dict, page: Optional[Page]):
        if page is None:
            return False, "no screen"
        if post.get("status_ok") and page.status >= 400:
            return False, f"HTTP {page.status}"
        if post.get("url") and mask_path(page.path) not in post["url"]:
            return False, f"expected {post['url']}, got {page.path!r} ({page.title})"
        if post.get("no_alerts") and page.alerts:
            return False, f"the app reported: {page.alerts}"
        return True, ""

    def _check_post(self, op: dict) -> None:
        post = op.get("post") or {}
        if not (post.get("url") or post.get("no_alerts")):
            return
        app = op["app"]
        page, why = None, ""
        for _ in range(self.max_fire + 1):
            page = self.env.page(app)
            ok, why = self._post_ok(post, page)
            if ok:
                return
            if not self._fire_handlers(app, page):
                break
        heads = [h for h in (page.headings if page else []) if h in self._new_text(app, page)]
        reason = "unknown_dialog" if heads and not (page and page.alerts) else "postcondition_failed"
        self._halt(reason, op, page=page, expected=post, message=f"after {op['action']} "
                   f"{(op.get('target') or {}).get('name') or ''!r}: {why}")

    def _file_dialog_check(self, op: dict) -> None:
        for _ in range(self.max_fire + 1):
            page = self.env.page(op["app"])
            if page is None:
                return
            if not self._fire_handlers(op["app"], page):
                self._halt("unknown_dialog", op, page=page,
                           message=f"{op['app']} is showing an unexpected dialog ({page.title})")

    def _observe(self, app: str) -> None:
        page = self.env.page(app)
        for t in (page.tables if page is not None else []):
            self.tables[(app, re.sub(r"\s*\(.*\)$", "", t["caption"]))] = t

    # ---------------------------------------------------------------- ops --
    _cur: Optional[dict] = None
    _vals: Optional[dict] = None

    def _exec(self, op: dict, vals: dict) -> None:
        a, app = op["action"], op["app"]
        web = self.env.is_web(app)
        self._cur, self._vals, self._fires_this_op = op, vals, 0
        entry = {"op": op["id"], "action": a, "app": app, "item": (self._item or {}).get("ref")}
        try:
            if a == "switch_app":
                if web:
                    self.env.switch(app)
                    self._observe(app)
            else:
                if not web:
                    self._file_dialog_check(op)
                getattr(self, f"_op_{a}", self._op_unknown)(op, vals, entry)
        except Halt:
            raise
        except (ElementNotFound, FileNotFoundError, FileExistsError, KeyError, ValueError, OSError,
                RuntimeError) as exc:
            self._halt("error", op, page=self.env.page(app) if web else None,
                       message=f"{type(exc).__name__}: {exc}")
        self.completed.append(op["id"])
        self.log.append(entry)

    def _op_unknown(self, op, vals, entry):
        self._halt("error", op, message=f"don't know how to {op['action']}")

    def _op_open_url(self, op, vals, entry):
        self.env.open(op["app"], render(op["target"]["url_path"], vals))
        self._observe(op["app"])
        self._check_post(op)

    def _op_click(self, op, vals, entry):
        el = self._resolve(op)
        if el is None:
            entry["skipped"] = "optional"
            return
        out = self.env.click(op["app"], el)
        if (op.get("post") or {}).get("download"):
            if not isinstance(out, Path):
                self._halt("postcondition_failed", op, page=self.env.page(op["app"]),
                           message=f"clicking {el.get('name')!r} did not download a file")
            self.last_download = Path(out).relative_to(self.env.home).as_posix()
            entry["downloaded"] = self.last_download
            return
        self._observe(op["app"])
        self._check_post(op)

    _op_download = _op_click

    def _op_type(self, op, vals, entry):
        el = self._resolve(op)
        if el is None:
            return
        value = self._value(op, vals)
        self.env.type(op["app"], el, value)
        entry["value"] = "<secret>" if isinstance(op.get("value"), dict) else value

    def _op_read(self, op, vals, entry):
        rel = render(op["target"]["path"], vals)
        if not self.env.exists(rel):
            if self.last_download:
                self.notes.append(f"read {self.last_download} (the file just downloaded) instead of {rel}")
                rel = self.last_download
            elif op.get("optional"):
                return
            else:
                self._halt("missing_file", op, message=f"{rel} does not exist")
        self.last_text = self.env.read(rel)
        self.last_table = csv_dicts(self.last_text) if rel.endswith(".csv") else None
        entry["path"] = rel

    def _op_mkdir(self, op, vals, entry):
        self.env.mkdir(render(op["target"]["path"], vals))

    def _op_move(self, op, vals, entry):
        src = render(op.get("from") or "{src_path}", vals)
        dst = render(op["to"], vals)
        want_sha = vals.get("src_sha256")
        if not self.env.exists(src):
            if self.env.exists(dst) and want_sha and self.env.sha(dst) == want_sha:
                entry["skipped"] = "already filed"
                return
            self._halt("missing_file", op, message=f"{src} is gone")
        sha = self.env.sha(src)
        if self.env.exists(dst):
            self._halt("file_conflict", op, message=f"{dst} already exists; I never overwrite files")
        self.env.move(src, dst)
        if not self.env.exists(dst) or self.env.sha(dst) != sha or self.env.exists(src):
            self._halt("postcondition_failed", op, message=f"move {src} -> {dst} did not take effect")
        entry.update({"from": src, "to": dst})

    def _op_append_rows(self, op, vals, entry):
        path = op["target"]["path"]
        if op.get("aggregate"):
            spec = op["aggregate"]
            if self.last_table is None:
                self._halt("missing_input_table", op, message="no table was read before this aggregate")
            rows = [[render(c, vals) for c in r] for r in aggregate(self.last_table, spec)]
            self.derived.update(agg_candidates(spec, rows))
        elif op.get("row"):
            rows = [[render(c, vals) for c in op["row"]]]
        else:
            self._halt("error", op, message="append without a row template")
        before = csv_rows(self.env.read_text(path)) if self.env.exists(path) else []
        if rows and all(r in before for r in rows):
            entry["skipped"] = "rows already present"
            return
        self.env.append_rows(path, rows)
        after = csv_rows(self.env.read_text(path))
        if after != before + rows:
            self._halt("postcondition_failed", op, message=f"{path}: rows not appended as expected")
        entry["rows"] = len(rows)

    def _op_write_file(self, op, vals, entry):
        path = render(op["target"]["path"], vals)
        allv = {**vals, **self.derived}
        text = "\n".join(render(ln, allv) for ln in op["lines"]) + "\n"
        if self.env.exists(path):
            if self.env.read_text(path) == text:
                entry["skipped"] = "identical file exists"
                return
            self._halt("file_conflict", op, message=f"{path} exists with other content; I never overwrite files")
        self.env.write(path, text)
        if self.env.read_text(path) != text:
            self._halt("postcondition_failed", op, message=f"{path} not written")
        entry["path"] = path

    # --------------------------------------------------------------- loop --
    def _run_loop(self) -> None:
        L = self.p["loop"]
        refs = self._enumerate(L)
        body = [o for o in L["body"] if not o.get("loop_select")]
        sel = next((o for o in L["body"] if o.get("loop_select")), None)
        seen: Dict[str, int] = {}
        for n, ref in enumerate(refs[: self.max_items]):
            self._item = {"index": n, "ref": ref["label"], "kind": None}
            self._cur, self._fires_this_op = sel, 0
            obs = self._open_item(L, ref, sel)
            self._item["kind"] = obs["kind"]
            vals, how = self._extract(L, obs, sel)
            if vals is None:
                self.skipped.append({"item": ref["label"], "why": how})
                continue
            key = json.dumps(sorted(vals.items()))
            if key in seen:
                self.skipped.append({"item": ref["label"], "why": f"duplicate of item {seen[key]}"})
                continue
            seen[key] = n
            rule = self._skip_rule(L, vals)
            if rule:
                self.skipped.append({"item": ref["label"], "why": rule})
                continue
            iv = {**vals, **obs.get("src", {}), "_how": how}
            allv = {**self.globals, **iv}
            for op in body:
                self._exec(op, allv)
            iv.pop("_how")
            self.items.append(iv)

    def _enumerate(self, L: dict) -> List[dict]:
        src = L["source"]
        if src["kind"] == "folder":
            return [{"label": rel, "path": rel} for rel in self.env.list_files(src["folder"])]
        app = src["app"]
        page = self.env.page(app)
        if page is None or mask_path(page.path) != mask_path(src["url_path"]):
            page = self.env.open(app, src["url_path"])
        refs, seen_pages = [], set()
        for _ in range(100):
            page = self.env.page(app)
            if page is None or page.path in seen_pages:
                break
            seen_pages.add(page.path)
            tables = [t for t in page.tables if re.match(re.escape(src["table"]) + r"\b", t["caption"])]
            if len(tables) != 1:
                self._halt("missing_control", None, page=page, app=app,
                           message=f"list table {src['table']!r} not found (or not unique) on {page.path}")
            names = {c for row in tables[0]["rows"] for c in row}
            for el in page.elements:
                if el["role"] == "link" and el.get("name") in names and el.get("href"):
                    refs.append({"label": el["name"], "href": el["href"]})
            nxt = [e for e in page.elements if e["role"] == "link" and NEXT_PAGE_RE.match(e.get("name", "").strip())]
            if not (self.p.get("policy", {}).get("follow_pagination") and len(nxt) == 1):
                break
            self.env.click(app, nxt[0])
            self.notes.append(f"followed pagination link {nxt[0]['name']!r}")
        return refs

    def _open_item(self, L: dict, ref: dict, sel: Optional[dict]) -> dict:
        src = L["source"]
        if src["kind"] == "folder":
            rel = ref["path"]
            text = self.env.read(rel)
            suffix = Path(rel).suffix.lower()
            return {"kind": suffix, "text": text,
                    "src": {"src_path": rel, "src_ext": suffix.lstrip("."), "src_sha256": self.env.sha(rel)}}
        app = src["app"]
        page = self.env.open(app, ref["href"])
        self._observe(app)
        if not isinstance(page, Page) or page.status >= 400:
            self._halt("postcondition_failed", sel, page=self.env.page(app), message=f"could not open item {ref['label']!r}")
        return {"kind": mask_path(page.path), "text": page.text(), "page": page, "app": app}

    def _extract(self, L: dict, obs: dict, sel: Optional[dict]):
        profs = [p for p in L["profiles"] if p["kind"] == obs["kind"]]
        res = [match_profile(p, obs["text"], self.params, obs["kind"]) for p in profs]
        oks = [r for r in res if r["status"] == "ok"]
        page, app = obs.get("page"), obs.get("app") or (sel or {}).get("app")
        if oks:
            if len({json.dumps(r["values"], sort_keys=True) for r in oks}) == 1:
                return dict(oks[0]["values"]), "layout"
            self._halt("ambiguous_value", sel, page=page, text=obs["text"], app=app,
                       message=f"two learned layouts read different values: {[r['values'] for r in oks]}")
        amb = [r for r in res if r["status"] == "ambiguous"]
        if amb:
            self._halt("ambiguous_value", sel, page=page, text=obs["text"], app=app,
                       message=f"value is ambiguous on this item ({amb[0]['why']})")
        lines = [ln.strip() for ln in obs["text"].splitlines()] + list((page.headings if page else []))
        for g in L.get("skip_unmatched", []):
            if g["kind"] == obs["kind"] and all(any(re.fullmatch(x["re"], ln) for ln in lines) for x in g["lines"]):
                return None, "taught: not an item"
        if L.get("on_unmatched") == "skip":
            return None, "not an item (no learned layout matches; the demo left such files alone)"
        if self.agent is not None and L.get("agent_step"):
            return self._agent_extract(L, obs, sel)
        self._halt("unmatched_item", sel, page=page, text=obs["text"], app=app,
                   message=f"item {(self._item or {}).get('ref')!r} does not match any example I was shown",
                   tried=[r["why"] for r in res])

    def _agent_extract(self, L: dict, obs: dict, sel: Optional[dict]):
        step = L["agent_step"]
        req = {"step": step["id"], "kind": "extract_item", "returns": step["returns"], "text": obs["text"][:6000],
               "instructions": "Return the listed values exactly as they apply to this item, or not_an_item.",
               "max_calls": step.get("max_calls_per_item", 1)}
        self.model_calls += 1
        resp = self.agent(req) or {}
        if resp.get("not_an_item"):
            return None, "agent: not an item"
        vals = {}
        for r in step["returns"]:
            v, prob = normalize_value(r["type"], str((resp.get("values") or {}).get(r["name"], "")), self.params[r["name"]])
            if v is None:
                self._halt("agent_invalid", sel, text=obs["text"], app=obs.get("app"),
                           message=f"agent step returned an invalid {r['type']} for {r['name']!r}")
            vals[r["name"]] = v
        return vals, "agent"

    def _skip_rule(self, L: dict, vals: dict) -> Optional[str]:
        for r in L.get("skip_rules", []):
            t = self.tables.get((r["app"], re.sub(r"\s*\(.*\)$", "", r["table"])))
            if not t or r["column"] not in t["header"]:
                continue
            j = t["header"].index(r["column"])
            if vals.get(r["param"]) in [row[j] for row in t["rows"] if j < len(row)]:
                return f"{r['param']}={vals.get(r['param'])!r} already in {r['table']}"
        return None

    # --------------------------------------------------------- self-check --
    def _self_check(self, start_text: dict, start_files: dict) -> dict:
        end_tables: Dict[str, dict] = {}
        for (app, cap), t in self.tables.items():
            end_tables.setdefault(app, {})[cap] = [dict(zip(t["header"], r)) for r in t["rows"]]
        bindings = {"global": dict(self.globals), "items": [dict(it) for it in self.items]}
        try:
            res = evaluate_check({"check": self.p["check"], "bindings": bindings}, start_text, self.env.home,
                                 end_tables, bindings, start_files)
        except Exception as exc:          # a broken check is "unverified", never "verified"
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "results": []}
        return {"ok": all(r["passed"] for r in res), "results": res}


ASK = {
    "unknown_dialog": "Something I haven't seen is on screen. Show me how you get past it (a short fix demonstration).",
    "missing_control": "I can't find the control for this step. Show me which control to use.",
    "ambiguous_match": "More than one control could be the one for this step. Show me which one.",
    "postcondition_failed": "The app did not end up where the demonstration did. Show me what to do here.",
    "unmatched_item": "This item doesn't look like any example. Show me how to handle it (or that it should be "
                      "left alone), or record another example.",
    "ambiguous_value": "A value on this item can be read more than one way. Record another example.",
}


# ================================================================ testbed ==

class TestbedEnv:
    """The run's surfaces on a seeded instance: the stdlib AX browser + the scripted file actor."""

    def __init__(self, home: Path, urls: Dict[str, str], context: dict, *, sink=None,
                 secrets: Optional[Callable[[str], Optional[str]]] = None):
        self.home = Path(home)
        self.context = dict(context or {})
        self.browser = Browser(urls, self.home / "Downloads", sink)
        self.files = FileActor(self.home, sink)
        self._secrets = secrets

    def set_sink(self, sink) -> None:
        self.browser.sink = sink
        self.files.sink = sink

    @property
    def sink(self):
        return self.browser.sink

    def is_web(self, app: str) -> bool:
        return app in self.browser.base_urls

    def page(self, app: str) -> Optional[Page]:
        return self.browser.pages.get(app) if self.is_web(app) else None

    def switch(self, app: str):
        return self.browser.switch(app)

    def open(self, app: str, path: str):
        return self.browser.open(app, path)

    def _front(self, app: str) -> None:
        if self.browser.app != app:
            self.browser.switch(app)

    def click(self, app: str, el: dict):
        self._front(app)
        return self.browser.click(el["role"], el.get("name"), ordinal=el.get("ordinal"))

    def type(self, app: str, el: dict, value: str):
        self._front(app)
        return self.browser.type(el.get("name"), value, ordinal=el.get("ordinal"))

    def list_files(self, folder: str) -> List[str]:
        d = self.home / folder
        if not d.is_dir():
            return []
        return sorted(f"{folder}/{p.name}" for p in d.iterdir() if p.is_file() and not p.name.startswith("."))

    def exists(self, rel: str) -> bool:
        return (self.home / rel).exists()

    def sha(self, rel: str) -> str:
        return sha256_file(self.home / rel)

    def read(self, rel: str) -> str:
        return self.files.view(rel)

    def read_text(self, rel: str) -> str:
        return (self.home / rel).read_text(encoding="utf-8")

    def move(self, src: str, dst: str) -> None:
        self.files.move(src, dst)

    def append_rows(self, rel: str, rows) -> None:
        self.files.append_rows(rel, rows)

    def write(self, rel: str, text: str) -> None:
        self.files.write(rel, text)

    def mkdir(self, rel: str) -> None:
        self.files.mkdir(rel)

    def secret(self, name: str) -> Optional[str]:
        if self._secrets:
            return self._secrets(name)
        for f in sorted((self.home / ".config").glob("*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except ValueError:
                continue
            for k, v in d.items():
                if k.lower() == (name or "").lower():
                    return v
        return None

    def actions(self) -> int:
        return self.browser.actions + self.files.actions


class DialogInjectingEnv:
    """Fault injection: after `after_actions` actions on `app`, an unexpected modal covers it
    until one of its buttons is clicked. Works for web apps and for file apps (a Finder sheet)."""

    def __init__(self, base, app: str, *, after_actions: int = 0, title: str = "Session about to expire",
                 lines=("For your security you will be signed out in 2 minutes.",),
                 buttons=("Stay signed in", "Sign out"), dismiss: Optional[str] = None):
        self.base, self.app, self.after = base, app, after_actions
        self.title, self.lines, self.buttons = title, list(lines), list(buttons)
        self.dismiss = dismiss or buttons[0]
        self.count, self.state, self._page = 0, "pending", None

    def __getattr__(self, k):
        return getattr(self.base, k)

    def _showing(self, app: str) -> bool:
        if app == self.app and self.state == "pending" and self.count >= self.after:
            self.state = "showing"
        return app == self.app and self.state == "showing"

    def _dialog(self) -> Page:
        if self._page is None:
            under = self.base.page(self.app)
            url = under.url if under is not None else "file:///dialog"
            from html import escape as E
            html = (f"<!doctype html><html><head><title>{E(self.title)}</title></head><body><main>"
                    f"<div role=\"dialog\" aria-modal=\"true\"><h2>{E(self.title)}</h2>"
                    + "".join(f"<p>{E(ln)}</p>" for ln in self.lines)
                    + "".join(f"<form method=\"post\" action=\"/__dialog\"><button type=\"submit\">{E(b)}</button></form>"
                              for b in self.buttons) + "</div></main></body></html>")
            self._page = Page(url, 200, html)
        return self._page

    def _blocked(self, app: str) -> None:
        if self._showing(app):
            raise RuntimeError(f"{app} is covered by a modal dialog ({self.title!r})")
        if app == self.app:
            self.count += 1

    def page(self, app: str):
        return self._dialog() if self._showing(app) else self.base.page(app)

    def click(self, app: str, el: dict):
        if self._showing(app):
            dlg = self._dialog()
            if el.get("name") not in self.buttons:
                raise ElementNotFound(f"{el.get('name')!r} is covered by a modal dialog")
            sink = self.base.browser.sink or self.base.files.sink
            if sink:
                sink({"type": "pointer", "kind": "click", "app": app, "element": ax_element(el), "window": dlg.summary()})
            self.state = "dismissed"
            under = self.base.page(app)
            if sink and under is not None:
                sink({"type": "ax", "app": app, "notification": "AXLoadComplete", "window": under.summary(with_text=True)})
            return under
        self._blocked(app)
        return self.base.click(app, el)

    def type(self, app, el, value):
        self._blocked(app)
        return self.base.type(app, el, value)

    def open(self, app, path):
        self._blocked(app)
        return self.base.open(app, path)

    def move(self, src, dst):
        self._blocked("Finder")
        return self.base.move(src, dst)

    def append_rows(self, rel, rows):
        self._blocked("Numbers")
        return self.base.append_rows(rel, rows)

    def write(self, rel, text):
        self._blocked("TextEdit")
        return self.base.write(rel, text)


# the per-task injected dialog used by tests and the CLI (app, actions on that app before it appears)
INJECTIONS = {
    "t1_statements": {"instance": "i02", "app": "Finder", "after_actions": 0, "title": "Allow access to Documents?",
                      "lines": ["“Finder” would like to access files in your Documents folder."],
                      "buttons": ["Allow", "Don’t Allow"], "dismiss": "Allow"},
    "t2_orders": {"instance": "i02", "app": "orders", "after_actions": 4},
    "t3_invoices": {"instance": "i03", "app": "accounting", "after_actions": 2, "title": "New version available",
                    "lines": ["Accounting was updated. Reload to get the latest features."],
                    "buttons": ["Later", "Reload now"], "dismiss": "Later"},
}


def injector(task: str) -> Callable:
    cfg = dict(INJECTIONS[task])
    app = cfg.pop("app")
    cfg.pop("instance", None)
    return lambda env: DialogInjectingEnv(env, app, **cfg)


def _verifier():
    from .run import verifier
    return verifier


def run_program_on(program: dict, I, urls: Dict[str, str], *, agent=None, env_wrap=None, fuzzy: bool = True,
                   env: Any = None):
    ctx = read_json(I.truth_path)["context"]         # 'today' only (what run.write_brief also hands an agent)
    if env is None:
        env = TestbedEnv(I.home, {a: urls[a] for a in program["web_apps"] if a in urls}, ctx)
        if env_wrap:
            env = env_wrap(env)
    res = SkillRunner(program, env, agent=agent, fuzzy=fuzzy).run()
    if res.get("halt"):
        res["halt"]["instance"] = I.inst
    return res, env


def run_instance(program: dict, run_dir: Path, task: str, inst: str, **kw) -> dict:
    from .seed import seed_instance
    from .webapps import Servers
    I = seed_instance(Path(run_dir), task, inst)
    with Servers(I) as urls:
        res, _env = run_program_on(program, I, urls, **kw)
    v = _verifier().verify_instance(I.hidden, I.home)
    return {"run": res, "verify": v, "success": bool(v["success"])}


def _brief(r: dict) -> dict:
    run = r["run"]
    return {"status": run["status"], "success": r["success"], "model_calls": run["model_calls"],
            "halt": (run.get("halt") or {}).get("reason"), "handlers_fired": len(run["handlers_fired"])}


def testbed_gate(task: str, canary_inst: str, work_dir: Path, *, canary_wrap=None, demo_inst: str = DEMO_INSTANCE,
                 require_canary_success: bool = False, fuzzy: bool = True) -> Callable[[dict, dict], dict]:
    """Re-gate a taught candidate: the demo instance must still pass with 0 model calls, and the
    canary (a fresh copy of the instance that halted) must not halt at the taught point again."""
    def gate(candidate: dict, halt: dict) -> dict:
        d = Path(work_dir) / f"gate_{task}_v{candidate['version']}_{canary_inst}"
        demo = run_instance(candidate, d, task, demo_inst, fuzzy=fuzzy)
        can = run_instance(candidate, d, task, canary_inst, env_wrap=canary_wrap, fuzzy=fuzzy)
        h = can["run"].get("halt") or {}
        repeats = (can["run"]["status"] == "HALTED" and h.get("reason") == halt.get("reason")
                   and h.get("op_id") == halt.get("op_id")
                   and (h.get("item") or {}).get("ref") == (halt.get("item") or {}).get("ref"))
        ok = (demo["success"] and demo["run"]["status"] == "VERIFIED" and demo["run"]["model_calls"] == 0
              and not repeats and (can["success"] or not require_canary_success))
        return {"ok": bool(ok), "demo": _brief(demo), "canary": _brief(can), "canary_success": can["success"],
                "repeats_halt": repeats}
    return gate


# ----------------------------------------------- human stand-in for fixes --

DISMISS_RE = r"^(continue|ok|allow|later|not now|stay signed in|keep|dismiss|close)$"


def scripted_fix(env, halt: dict, task: str, inst: str) -> None:
    """Play the human who demonstrates the fix on the halted screen (knows the instance, like
    demonstrate.py's scripted human; never reads ground truth files)."""
    reason = halt["reason"]
    app = (halt.get("observed") or {}).get("app")
    if reason in ("unknown_dialog", "postcondition_failed", "missing_control", "ambiguous_match"):
        page = env.page(app)
        if page is not None:
            btns = [e for e in page.elements if e["role"] == "button" and re.match(DISMISS_RE, e.get("name", ""), re.I)]
            if btns:
                env.click(app, btns[0])
                return
        op = halt.get("op") or {}
        tgt = op.get("target") or {}
        role = ROLE.get(tgt.get("role"), tgt.get("role"))
        if page is not None and role:
            # the human reads the screen: the control that means the same thing, ignoring fields already filled in
            els = [e for e in page.elements if e["role"] == role and not (role == "textbox" and e.get("value"))]
            st, idx = fuzzy_pick(tgt.get("name", ""), [e.get("name", "") for e in els])
            if st == "ok":
                el = els[idx[0]]
                if role == "textbox":
                    env.type(app, el, halt.get("intended_value") or "")
                else:
                    env.click(app, el)
        return
    if reason == "unmatched_item" and task == "t3_invoices":
        from .seed import T3_SPECS
        text = (halt.get("observed") or {}).get("text", "")
        spec = next((m for m in T3_SPECS[inst]["msgs"] if re.search(r"(?<![\w-])" + re.escape(m[1]) + r"(?![\w-])", text)), None)
        if spec is None:                     # not an invoice: the human just goes back
            page = env.page("inbox")
            env.click("inbox", page.find("link", "Back to inbox"))
            return
        vendor, number, date, _raw_date, raw_amt, _tpl, _entered = spec
        env.switch("accounting")
        env.click("accounting", env.page("accounting").find("link", name_re=r"new bill"))
        for rx, val in ((r"vendor|supplier", vendor), (r"number", number), (r"date", date.isoformat()),
                        (r"amount|total", fmt_money(parse_money(raw_amt)))):
            env.type("accounting", env.page("accounting").find("textbox", name_re=rx), val)
        env.click("accounting", env.page("accounting").find("button", name_re=r"^save"))
        env.switch("inbox")
        env.click("inbox", env.page("inbox").find("link", "Back to inbox"))
        return
    raise ValueError(f"no scripted fix for {task}/{inst} {reason}")


def record_fix(env, I, halt: dict) -> Trace:
    rec = Recorder(I.home, task=I.task, instance=f"{I.inst}-fix", context=dict(env.context),
                   title="fix demonstration", note=f"fix for {halt['reason']} at {halt.get('op_id')}")
    env.set_sink(rec.feed)
    try:
        scripted_fix(env, halt, I.task, I.inst)
    finally:
        env.set_sink(None)
    return rec.finish()


def halt_teach_live(program: dict, run_dir: Path, task: str, inst: str, *, env_wrap=None, gate_dir: Path,
                    require_canary_success: bool = False, fuzzy: bool = True) -> dict:
    """Run -> HALTED -> scripted fix demo (on the live halted screen) -> teach + gate -> resume live."""
    from .seed import seed_instance
    from .webapps import Servers
    I = seed_instance(Path(run_dir), task, inst)
    out: Dict[str, Any] = {"task": task, "instance": inst, "from_version": program["version"]}
    with Servers(I) as urls:
        res, env = run_program_on(program, I, urls, env_wrap=env_wrap, fuzzy=fuzzy)
        out["first"] = {"status": res["status"], "model_calls": res["model_calls"]}
        if res["status"] != "HALTED":
            out["result"] = "no halt"
            return out
        halt = res["halt"]
        out["halt"] = {k: halt.get(k) for k in ("reason", "op_id", "message", "new_text")}
        out["halt"]["item"] = (halt.get("item") or {}).get("ref")
        fix = record_fix(env, I, halt)
        out["fix_steps"] = [f"{s.action} {s.target.get('name') or s.target.get('path') or ''}".strip() for s in fix.steps]
        gate = testbed_gate(task, inst, gate_dir, canary_wrap=env_wrap, require_canary_success=require_canary_success,
                            fuzzy=fuzzy)
        t = teach(program, halt, fix, gate=gate)
        out["teach"] = {"status": t["status"], "reason": t.get("reason"), "change": t.get("change"),
                        "gate": t.get("gate")}
        if t["status"] != "promoted":
            out["result"] = "refused"
            return out
        new = t["program"]
        res2, _ = run_program_on(new, I, urls, env=env, fuzzy=fuzzy)   # live resume on the same, halted instance
        out["resume"] = {"status": res2["status"], "model_calls": res2["model_calls"],
                         "halt": (res2.get("halt") or {}).get("reason")}
    v = _verifier().verify_instance(I.hidden, I.home)
    out["resume"]["success"] = bool(v["success"])
    out["program"] = new
    out["result"] = "pass" if v["success"] else "fail"
    return out


# ================================================================== CLI ==

def make_skill_arm(programs: Dict[str, dict], *, agent=None, fuzzy: bool = True, sink: Optional[dict] = None):
    """An arm for run.run_arm: run the induced skill program of the instance's task."""
    def arm(I, urls, brief) -> dict:
        res, _ = run_program_on(programs[I.task], I, urls, agent=agent, fuzzy=fuzzy)
        if sink is not None:
            sink[(I.task, I.inst)] = res
        return {"ok": res["status"] != "HALTED", "actions": res["actions"],
                "error": (res.get("halt") or {}).get("message", "")}
    return arm


def held_out_rates(report: dict) -> Dict[str, str]:
    return {t: f"{s['held_out_success']}/{s['held_out_n']}" for t, s in report["summary"].items() if t != "_all"}


def evaluate(run_dir: Path, tasks=TASKS, *, fuzzy: bool = True, quiet: bool = False) -> dict:
    """WP5 evaluation with ZERO model calls: induce v1 from the single demo, run held-out, then
    halt -> scripted fix -> teach rounds, re-run held-out, and the injected-dialog experiment."""
    from .run import prepare_demos, run_arm
    run_dir = Path(run_dir)
    demos = prepare_demos(run_dir, tasks)
    lib = SkillLibrary(run_dir / "skills")
    v1 = {}
    for t in tasks:
        r = induce([Trace.load(demos[t] / "trace.json")])
        if r.refused:
            raise SystemExit(f"{t}: induction refused: {r.record_next}")
        v1[t] = r.program
        lib.save(r.program)
        (demos[t] / "program.txt").write_text(render_program(r.program) + "\n", encoding="utf-8")
    out: Dict[str, Any] = {"fuzzy": fuzzy}
    runs: Dict[tuple, dict] = {}
    rep1 = run_arm(run_dir / "v1", "skill_v1", make_skill_arm(v1, fuzzy=fuzzy, sink=runs), demos, tasks)
    out["v1"] = {"held_out": held_out_rates(rep1),
                 "model_calls": sum(r["model_calls"] for r in runs.values()),
                 "halted": {f"{k[0]}/{k[1]}": r["halt"]["reason"] for k, r in runs.items() if r["status"] == "HALTED"},
                 "healed": {f"{k[0]}/{k[1]}": [h["rung"] + ":" + str(h["used"]) for h in r["healed"]]
                            for k, r in runs.items() if r["healed"]}}
    # ---- halt -> teach rounds on held-out halts (one scripted fix demonstration per round)
    cur = dict(v1)
    rounds = []
    for t in tasks:
        for inst in HELD_OUT:
            for _ in range(4):
                r = run_instance(cur[t], run_dir / "probe", t, inst, fuzzy=fuzzy)
                if r["run"]["status"] != "HALTED":
                    break
                lt = halt_teach_live(cur[t], run_dir / "teach", t, inst, gate_dir=run_dir / "gates", fuzzy=fuzzy)
                rounds.append({k: v for k, v in lt.items() if k != "program"})
                if lt.get("result") not in ("pass", "fail") or "program" not in lt:
                    break
                cur[t] = lt["program"]
                lib.save(cur[t])
    out["teach_rounds"] = rounds
    runs2: Dict[tuple, dict] = {}
    rep2 = run_arm(run_dir / "vN", "skill_vN", make_skill_arm(cur, fuzzy=fuzzy, sink=runs2), demos, tasks)
    out["vN"] = {"versions": {t: cur[t]["version"] for t in tasks}, "held_out": held_out_rates(rep2),
                 "model_calls": sum(r["model_calls"] for r in runs2.values()),
                 "halted": {f"{k[0]}/{k[1]}": r["halt"]["reason"] for k, r in runs2.items() if r["status"] == "HALTED"}}
    # ---- injected unexpected dialog: HALT -> one fix demo -> vN+1 passes (live + fresh canary)
    inj = []
    for t in tasks:
        inst = INJECTIONS[t]["instance"]
        lt = halt_teach_live(v1[t], run_dir / "inject", t, inst, env_wrap=injector(t), gate_dir=run_dir / "gates_inj",
                             require_canary_success=True, fuzzy=fuzzy)
        inj.append({k: v for k, v in lt.items() if k != "program"})
    out["injected_dialog"] = inj
    write_json(run_dir / "wp5_report.json", out)
    if not quiet:
        print(json.dumps({k: v for k, v in out.items() if k not in ("teach_rounds", "injected_dialog")}, indent=2))
        for r in rounds:
            print(f"teach {r['task']}/{r['instance']} v{r['from_version']}: halt={r.get('halt', {}).get('reason')} "
                  f"-> {r.get('teach', {}).get('status')} {r.get('teach', {}).get('change', {}).get('kind') if r.get('teach') else ''}"
                  f" -> resume {r.get('resume', {}).get('status')} success={r.get('resume', {}).get('success')}")
        for r in inj:
            print(f"inject {r['task']}/{r['instance']}: first={r['first']['status']} halt={r.get('halt', {}).get('reason')} "
                  f"teach={r.get('teach', {}).get('status')} canary={r.get('teach', {}).get('gate', {}).get('canary')} "
                  f"resume={r.get('resume')}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="WP5 skill runner evaluation (no model calls)")
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--strict", action="store_true", help="disable the unique-token-overlap grounding rung")
    ap.add_argument("--task", action="append", choices=TASKS)
    a = ap.parse_args(argv)
    run_dir = Path(a.run_dir or tempfile.mkdtemp(prefix="teach_once_wp5_"))
    print(f"run dir: {run_dir}")
    evaluate(run_dir, tuple(a.task or TASKS), fuzzy=not a.strict)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
