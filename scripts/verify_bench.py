#!/usr/bin/env python3
"""
verify_bench.py — false-VERIFIED benchmark for the expected-effects checker
(scaffold/agent/desktop/effects/expect.py) on App Factory tasks.

    PYTHONPATH=. python3 scripts/verify_bench.py [--family F] [--limit N] [--judge banner]
                                                 [--json out.json] [--quiet]

Per task (train split only, see EXAM INTEGRITY):
  1. DEMO: the scripted oracle runs once on a fresh app instance. Readback before and after
     (every nav view + every view the demo visited + <main> links of those, ``--crawl``
     levels deep; each read in a separate fresh session over the same backend state), plus
     the final page -> expect.induce() -> an Expectation.
  2. CASES on fresh instances, each checked with expect.check() (readback before / after):
       success           the oracle replayed
       success_detour    oracle, then /help and back before done (banner gone, state same)
       partial           the last state-changing step skipped
       wrong_value       the first fill gets a wrong value (or a select a different option)
       duplicate         the last state-changing step done twice
       extra_deletion    after the run, one unrelated item removed from the backend
       banner_no_effect  run completes (success banner on screen) but the backend is reverted
       wrong_answer      (answer tasks) done("...") without the value
       no_pause          (pause tasks) done instead of needs_user
  3. TRUTH = the factory's oracle verifier (scaffold/rl/factory/verify.py score()), over the
     backend state + event log, never seen by the checker.

The checker sees only rendered pages (htmldriver.parse of main content: row text, list
items, paragraphs, field values, pressed/expanded buttons; status/alert banners; dialogs
excluded) -- what the product's AX readback would see; never app.state.

Reports: false-VERIFIED rate = VERIFIED among truth-fail cases (target ~0); true-VERIFIED
rate = VERIFIED among truth-success cases; confusion truth x verdict; per-case breakdown.
A case whose sabotage cannot be applied (no fill step, step not found) is counted n/a.

EXAM INTEGRITY: only the factory "train" split is used; the frozen "ood" split (wizard,
files_tree, calendar) and MiniWoB are never touched. Output is a measurement, not training
data (it carries "not_training_data": true).
No network, no model calls, no cost: everything runs in-process (~seconds).
"""
from __future__ import annotations

import argparse
import copy
import json
import random
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scaffold.agent.desktop.effects.expect import (RUN_FAILED, RUN_VERDICTS,  # noqa: E402
                                                   RUN_VERIFIED, Expectation, Observation, check, induce)
from scaffold.rl.factory.app import App  # noqa: E402
from scaffold.rl.factory.catalog import Catalog, Entry, oracle_traj  # noqa: E402
from scaffold.rl.factory.htmldriver import El, SimBrowser, StepError, _Parser  # noqa: E402
from scaffold.rl.factory.server import Registry, inproc_transport  # noqa: E402
from scaffold.rl.factory.verify import score  # noqa: E402

SPLIT = "train"
CASES = ("success", "success_detour", "partial", "wrong_value", "duplicate", "extra_deletion",
         "banner_no_effect", "wrong_answer", "no_pause")
SUCCESS_CASES = ("success", "success_detour")
SKIP_ROLES = {"status", "alert", "dialog", "alertdialog"}
TEXT_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "caption", "dt", "dd", "label", "summary"}


class NA(Exception):
    """The sabotage does not apply to this task."""


# ── observation: what a readback of the page shows ────────────────────────────
def _skip(e: El) -> bool:
    a = e.attrs
    return (a.get("role") in SKIP_ROLES or e.tag in ("script", "style", "nav", "aside", "header")
            or "hidden" in a or a.get("aria-hidden") == "true" or "overlay" in a.get("class", "").split())


def atoms(html: str) -> List[str]:
    """Multiset of content atoms in <main> (banners and dialogs excluded)."""
    p = _Parser()
    p.feed(html)
    main = next((e for e in p.root.iter() if e.tag == "main"), None)
    out: List[str] = []
    if main is None:
        return out

    def walk(e: El) -> None:
        for c in e.children:
            if not isinstance(c, El) or _skip(c):
                continue
            a, t = c.attrs, c.tag
            if t == "tr":
                out.append("row:" + c.text())
            elif t == "li":
                out.append("item:" + c.text())
            elif t in TEXT_TAGS and c.text():
                out.append("text:" + c.text())
            if t == "input" and a.get("type", "text") != "hidden" and a.get("name") and not a["name"].startswith("__"):
                if a.get("type") in ("checkbox", "radio"):
                    out.append(f"field:{a['name']}={a.get('value', 'on')}:{'on' if 'checked' in a else 'off'}")
                else:
                    out.append(f"field:{a['name']}={a.get('value', '')}")
            elif t == "select" and a.get("name"):
                opts = [o for o in c.iter() if o.tag == "option"]
                ch = next((o for o in opts if "selected" in o.attrs), opts[0] if opts else None)
                out.append(f"field:{a['name']}=" + (ch.attrs.get("value", ch.text()) if ch is not None else ""))
            elif t == "textarea" and a.get("name"):
                out.append(f"field:{a['name']}={c.text(skip_hidden=False)}")
            if t == "button" or a.get("role") == "button":
                st = [f"{k}={a[k]}" for k in ("aria-pressed", "aria-expanded") if k in a]
                if st:
                    out.append(f"button:{c.text() or a.get('title', '')}:{','.join(st)}")
            walk(c)
    walk(main)
    return out


_READ_CACHE: Dict[tuple, Optional[List[str]]] = {}


class Session:
    """One fresh App instance + an in-process browser on it."""

    def __init__(self, entry: Entry, iid: str):
        self.entry = entry
        self.reg = Registry()
        self.app = App(entry.spec, iid=iid)
        self.reg.add(self.app)
        self.base = f"http://127.0.0.1:0/t/{iid}/"
        self.br = SimBrowser(inproc_transport(self.reg))

    def readback(self, keys: List[str]) -> Dict[str, Optional[List[str]]]:
        """Separate-session readback: each key is read by a FRESH App instance over a copy of
        this run's backend state, so no UI state of the acting session (unsaved drafts, a
        filter, a sort, an open tab, a dismissed popup) leaks into what is read back."""
        out: Dict[str, Optional[List[str]]] = {}
        sk = json.dumps(self.app.state, sort_keys=True, default=str)
        for k in keys:
            ck = (self.entry.task.id, sk, k)             # same state + same view -> same page
            if ck in _READ_CACHE:
                out[k] = _READ_CACHE[ck]
                continue
            try:
                reader = App(self.entry.spec, iid="rb")
                reader.state = copy.deepcopy(self.app.state)
                reg = Registry()
                reg.add(reader)
                st, html, _ = inproc_transport(reg)("GET", "http://127.0.0.1:0/t/rb/" + k, None)
                out[k] = atoms(html) if st == 200 else None
            except Exception:
                out[k] = None
            if len(_READ_CACHE) > 20000:
                _READ_CACHE.clear()
            _READ_CACHE[ck] = out[k]
        return out

    def final(self, answer: Optional[str], ended: Optional[str]) -> Observation:
        v = self.br.view
        return Observation(url=self.br.url, text=v.text if v else None, statuses=list(v.statuses) if v else None,
                           alerts=list(v.alerts) if v else None, answer=answer, ended=ended)


def read_keys(entry: Entry, visits: List[str]) -> List[str]:
    from scaffold.rl.factory.app import FAMILIES
    nav = [v for v, _ in FAMILIES[entry.spec.family].nav(App(entry.spec, iid="k"))]
    keys = list(dict.fromkeys([entry.spec.start_view] + nav + [v for v in visits if v.split("?")[0] != "help"]))
    return keys


def main_links(html: str, base: str) -> List[str]:
    """Same-app GET links inside <main> (pagination, detail pages, tabs) as view keys."""
    p = _Parser()
    p.feed(html)
    main = next((e for e in p.root.iter() if e.tag == "main"), None)
    if main is None:
        return []
    path = base.split("127.0.0.1:0", 1)[-1]
    out = []
    for e in main.iter():
        h = e.attrs.get("href", "") if e.tag == "a" else ""
        if h.startswith(path):
            out.append(h[len(path):])
    return out


def discover(entry: Entry, keys: List[str], depth: int, cap: int) -> List[str]:
    """Widen the readback: follow <main> links ``depth`` levels from ``keys`` (fresh instance)."""
    s = Session(entry, "crawl")
    seen = list(keys)
    frontier = list(keys)
    for _ in range(depth):
        nxt = []
        for k in frontier:
            s.br.open(s.base + k)
            for k2 in main_links(s.br.html, s.base):
                if k2 and k2.split("?")[0] != "help" and k2 not in seen and len(seen) < cap:
                    seen.append(k2)
                    nxt.append(k2)
        frontier = nxt
    return seen


def norm_url(u: str) -> str:
    return re.sub(r"/t/[^/]+/", "/t/*/", u)


# ── running a (possibly sabotaged) episode ────────────────────────────────────
def _writes(entry: Entry) -> List[int]:
    """Indexes of oracle steps that changed the backend (one or more POST events)."""
    s = Session(entry, "w")
    s.br.open(s.base + entry.spec.start_view)
    out = []
    for i, st in enumerate(entry.steps):
        n = len(s.app.events)
        s.br.run_step(st)
        if i == 0 and entry.spec.popup:              # catalog puts the popup dismissal first (ui only)
            continue
        if len(s.app.events) > n and any(not e.get("blocked") for e in s.app.events[n:]):
            out.append(i)
    return out


def _mutate_value(v: str) -> str:
    m = re.search(r"\d(?!.*\d)", v)
    if m:
        d = str((int(m.group()) + 1) % 10)
        return v[:m.start()] + d + v[m.end():]
    return (v + " x") if v else "x"


def run_case(entry: Entry, case: str, iid: str, keys: List[str], writes: List[int],
             rng: random.Random) -> Tuple[Observation, Observation, Session, List[dict]]:
    kind = entry.task.kind
    steps = [dict(s) for s in entry.steps]
    if case in ("partial", "duplicate", "banner_no_effect", "wrong_value") and kind != "act":
        raise NA(f"{case} only applies to act tasks")
    if case == "wrong_answer" and kind != "answer":
        raise NA("answer tasks only")
    if case == "no_pause" and kind != "pause":
        raise NA("pause tasks only")
    dup_at = None
    if case == "partial":
        if not writes:
            raise NA("no state-changing step")
        steps.pop(writes[-1])
    elif case == "duplicate":
        if not writes:
            raise NA("no state-changing step")
        dup_at = writes[-1]
    elif case == "wrong_value":
        idx = next((i for i, s in enumerate(steps) if s["op"] in ("fill", "select")), None)
        if idx is None:
            raise NA("no fill/select step")
        steps[idx]["__wrong"] = True
    elif case == "wrong_answer":
        steps[-1] = {"op": "done", "text": "I could not find it."}
    elif case == "no_pause":
        steps[-1] = {"op": "done", "text": "Done."}
    s = Session(entry, iid)
    before = Observation(reads=s.readback(keys))
    s.br.open(s.base + entry.spec.start_view)
    done: List[dict] = []
    for i, st in enumerate(steps):
        if case == "success_detour" and st["op"] in ("done", "needs_user"):
            here = s.br.url
            s.br.open(s.base + "help")
            s.br.open(here)
        if st.pop("__wrong", False):
            if st["op"] == "fill":
                st["value"] = _mutate_value(st["value"])
            else:
                c = s.br.find(st["role"], st["name"], st.get("context"))
                others = [t for _, t in c.options if t.strip().lower() != st["option"].strip().lower()]
                if not others:
                    raise NA("select has no other option")
                st["option"] = rng.choice(others)
        try:
            s.br.run_step(st)
            if i == dup_at:
                s.br.run_step(st)
        except StepError as e:
            raise NA(f"sabotaged run could not proceed: {e}")
        done.append(st)
    last = done[-1] if done else {}
    answer = last.get("text") if last.get("op") == "done" else None
    ended = last.get("op") if last.get("op") in ("done", "needs_user") else None
    after = s.final(answer, ended)
    if case == "extra_deletion":
        _delete_unrelated(s.app, entry, rng)
    elif case == "banner_no_effect":
        if s.app.state == entry.spec.data:
            raise NA("the run changed nothing")
        s.app.state = copy.deepcopy(entry.spec.data)
    after.reads = s.readback(keys)
    return before, after, s, done


def _delete_unrelated(app: App, entry: Entry, rng: random.Random) -> None:
    touched = {tuple(map(str, op["path"][:2])) for op in entry.task.meta.get("expect", [])}
    cands = []
    for k, v in app.state.items():
        if isinstance(v, dict) and len(v) >= 2 and all(isinstance(x, dict) for x in v.values()):
            cands += [(k, kk) for kk in v if (str(k), str(kk)) not in touched]
        elif isinstance(v, list) and len(v) >= 2:
            cands += [(k, i) for i in range(len(v)) if (str(k), str(i)) not in touched]
    if not cands:
        raise NA("no unrelated collection item to delete")
    k, kk = rng.choice(sorted(cands, key=str))
    if isinstance(app.state[k], list):
        app.state[k].pop(kk)
    else:
        app.state[k].pop(kk)


def visible(after: Observation, ref: Observation) -> bool:
    """Could ANY page-reading checker tell this run from the clean replay? False = the fault
    landed outside everything the readback shows (no UI-only verifier could catch it)."""
    if (after.answer, after.ended, after.alerts) != (ref.answer, ref.ended, ref.alerts):
        return True
    return any(Counter(after.reads.get(k) or []) != Counter(ref.reads.get(k) or [])
               for k in set(after.reads) | set(ref.reads))


def truth(entry: Entry, s: Session, steps: List[dict]) -> bool:
    t = entry.task
    sc = score(t.meta, t.kind, entry.spec.data, s.app.state, s.app.events, oracle_traj(t.id, steps),
               entry.spec.injection_values)
    return bool(sc.success)


# ── judges (optional second opinion; may only veto) ───────────────────────────
def banner_judge(exp: Expectation, after: Observation, res) -> Optional[str]:
    """Toy deterministic stand-in for a model judge: veto act runs with no status banner."""
    if exp.kind == "act" and not after.statuses:
        return "no confirmation banner on the final page"
    return None


JUDGES: Dict[str, Callable] = {"banner": banner_judge}


# ── benchmark ────────────────────────────────────────────────────────────────
def select_entries(cat: Catalog, family: Optional[str] = None, limit: Optional[int] = None) -> List[Entry]:
    out = [e for e in cat.entries.values() if e.task.split == SPLIT and (family is None or e.task.family == family)]
    assert all(e.task.split == "train" for e in out), "exam leak: non-train task selected"
    return out[:limit] if limit else out


def bench(family: Optional[str] = None, limit: Optional[int] = None, judge: Optional[Callable] = None,
          crawl: int = 1, cap: int = 60, log: Callable[[str], None] = lambda s: None) -> dict:
    t0 = time.time()
    cat = Catalog([family] if family else None)
    entries = select_entries(cat, family, limit)
    rows: List[dict] = []
    induced = Counter()
    for n, e in enumerate(entries):
        rng = random.Random(e.task.id)
        try:
            writes = _writes(e)
            demo = Session(e, f"d{n}")
            keys0 = read_keys(e, [])
            demo_before = Observation(reads=demo.readback(keys0))
            demo.app.visits.clear()
            demo.br.open(demo.base + e.spec.start_view)
            for st in e.steps:
                demo.br.run_step(st)
            last = e.steps[-1]
            keys = discover(e, read_keys(e, list(demo.app.visits)), crawl, cap)
            demo_after = demo.final(last.get("text") if last["op"] == "done" else None,
                                    last["op"] if last["op"] in ("done", "needs_user") else None)
            demo_after.reads = demo.readback(keys)
            # reads of keys first visited during the demo: take the before-view from a fresh instance
            demo_before.reads.update(Session(e, f"b{n}").readback([k for k in keys if k not in keys0]))
            exp = induce(demo_before, demo_after, kind=e.task.kind, url_normalize=norm_url)
            induced["observable" if exp.observable else "unobservable"] += 1
        except Exception as ex:                      # demo itself broke: report, do not score
            rows.append({"task": e.task.id, "case": "demo", "na": f"{type(ex).__name__}: {ex}"})
            continue
        ref: Optional[Observation] = None                # the clean replay, to judge fault visibility
        for case in CASES:
            try:
                before, after, s, steps = run_case(e, case, f"c{n}{case[:3]}", keys, writes, rng)
            except NA as why:
                rows.append({"task": e.task.id, "case": case, "na": str(why)})
                continue
            ok = truth(e, s, steps)
            res = check(exp, before, after, judge=judge)
            if case == "success":
                ref = after
            rows.append({"task": e.task.id, "kind": e.task.kind, "family": e.task.family, "case": case,
                         "truth": "success" if ok else "fail", "verdict": res.verdict, "reason": res.reason,
                         "judge_veto": bool(res.judge and res.judge.get("veto")),
                         "fault_visible": None if ok or ref is None else visible(after, ref)})
        log(f"{n + 1}/{len(entries)} {e.task.id}")
    return summarize(rows, entries, induced, time.time() - t0)


def summarize(rows: List[dict], entries: List[Entry], induced: Counter, secs: float) -> dict:
    scored = [r for r in rows if "verdict" in r]
    conf = {t: {v: 0 for v in RUN_VERDICTS} for t in ("success", "fail")}
    by_case: Dict[str, Dict[str, int]] = defaultdict(lambda: {"n": 0, "truth_success": 0, **{v: 0 for v in RUN_VERDICTS}})
    by_kind: Dict[str, Dict[str, int]] = defaultdict(lambda: {"n": 0, **{v: 0 for v in RUN_VERDICTS}})
    for r in scored:
        conf[r["truth"]][r["verdict"]] += 1
        c = by_case[r["case"]]
        c["n"] += 1
        c["truth_success"] += r["truth"] == "success"
        c[r["verdict"]] += 1
        by_kind[r["kind"]]["n"] += 1
        by_kind[r["kind"]][r["verdict"]] += 1
    fails = sum(conf["fail"].values())
    succ = sum(conf["success"].values())
    false_v = [r for r in scored if r["truth"] == "fail" and r["verdict"] == RUN_VERIFIED]
    fails_vis = [r for r in scored if r["truth"] == "fail" and r.get("fault_visible")]
    false_v_vis = [r for r in fails_vis if r["verdict"] == RUN_VERIFIED]
    false_f = [r for r in scored if r["truth"] == "success" and r["verdict"] == RUN_FAILED]
    na = Counter(r["case"] for r in rows if "na" in r)
    return {
        "split": SPLIT, "not_training_data": True, "tasks": len(entries), "seconds": round(secs, 1),
        "induced": dict(induced), "cases_scored": len(scored), "cases_na": dict(na),
        "false_verified_rate": round(len(false_v) / fails, 4) if fails else None,
        "fail_cases": fails, "fail_cases_visible": len(fails_vis),
        "false_verified_rate_visible": round(len(false_v_vis) / len(fails_vis), 4) if fails_vis else None,
        "true_verified_rate": round(conf["success"][RUN_VERIFIED] / succ, 4) if succ else None,
        "false_failed_rate": round(len(false_f) / succ, 4) if succ else None,
        "truth_fail_caught_rate": round(conf["fail"][RUN_FAILED] / fails, 4) if fails else None,
        "confusion": conf, "by_case": dict(by_case), "by_kind": dict(by_kind),
        "false_verified": [{k: r[k] for k in ("task", "case", "reason", "fault_visible")} for r in false_v][:50],
        "false_failed": [{k: r[k] for k in ("task", "case", "reason")} for r in false_f][:50],
        "demo_errors": [r for r in rows if r.get("case") == "demo"][:20],
    }


def render(rep: dict) -> str:
    L = [f"verify_bench: {rep['tasks']} train tasks, {rep['cases_scored']} scored cases, {rep['seconds']}s",
         f"induced expectations: {rep['induced']}",
         f"false-VERIFIED rate  {rep['false_verified_rate']}   (VERIFIED among truth-fail; target ~0)",
         f"  on faults visible to the readback: {rep['false_verified_rate_visible']}  "
         f"({rep['fail_cases_visible']} of {rep['fail_cases']} truth-fail cases are visible at all)",
         f"true-VERIFIED rate   {rep['true_verified_rate']}   (VERIFIED among truth-success)",
         f"false-FAILED rate    {rep['false_failed_rate']}   (FAILED among truth-success)",
         f"fail caught (FAILED) {rep['truth_fail_caught_rate']}",
         "", "confusion (truth x verdict):", f"  {'':8s} {'verified':>9s} {'unverified':>11s} {'failed':>7s}"]
    for t in ("success", "fail"):
        c = rep["confusion"][t]
        L.append(f"  {t:8s} {c['verified']:9d} {c['unverified']:11d} {c['failed']:7d}")
    L += ["", f"  {'case':17s} {'n':>4s} {'truth_ok':>8s} {'verified':>9s} {'unverif':>8s} {'failed':>7s} {'n/a':>5s}"]
    for case in CASES:
        c = rep["by_case"].get(case)
        if c:
            L.append(f"  {case:17s} {c['n']:4d} {c['truth_success']:8d} {c['verified']:9d} {c['unverified']:8d} "
                     f"{c['failed']:7d} {rep['cases_na'].get(case, 0):5d}")
    if rep["false_verified"]:
        L += ["", "false VERIFIED (first 10):"] + [
            f"  {r['task']} {r['case']}{'' if r['fault_visible'] else '  (fault invisible to readback)'}"
            for r in rep["false_verified"][:10]]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--family")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--judge", choices=sorted(JUDGES))
    ap.add_argument("--crawl", type=int, default=1, help="readback: follow <main> links this many levels (0 = "
                    "only nav views + views the demo visited)")
    ap.add_argument("--cap", type=int, default=60, help="max readback views per task")
    ap.add_argument("--json")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    rep = bench(a.family, a.limit, JUDGES.get(a.judge) if a.judge else None, a.crawl, a.cap,
                log=(lambda s: None) if a.quiet else (lambda s: print(s, file=sys.stderr)))
    rep.update(judge=a.judge, crawl=a.crawl, cap=a.cap)
    print(render(rep))
    if a.json:
        Path(a.json).write_text(json.dumps(rep, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
