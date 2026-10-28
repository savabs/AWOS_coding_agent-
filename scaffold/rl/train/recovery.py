"""
recovery.py — anti-loop / recovery SFT rows from oracle paths with one injected mistake (plan change 4).

    python -m scaffold.rl.train.recovery --out runs/rl/recovery/rows.jsonl \\
        [--sources factory,miniwob] [--types repeat,pingpong,...] [--per-type 1] [--workers 2]
        [--families docs_hub,prefs] [--miniwob-seeds 1000-1009] [--limit N] [--reason none|template] [--dry-run]

Why: the checkpoint-150 exam lost 12 of 14 MiniWoB tasks to tab-switching loops. The model never saw a
state where its own history shows a mistake or a loop, and never saw the move that gets out of it.

How (TRAIN tasks only: factory split "train", MiniWoB non-holdout types at seeds >= TRAIN_SEED_BASE):
  1. clean run: the oracle through the REAL harness (MicroAgent + headless Chromium, guards=product); the
     task is used only if it succeeds. Its emitted actions (with the control each resolved to) are the
     reference path.
  2. for each perturbation type, pick an injection point k (number of oracle actions already taken) and
     re-run: oracle prefix -> injected mistake(s) -> a recovery candidate -> the rest of the oracle.
         wrong_click   click an unrelated link/tab (or checkbox) instead of the next oracle action
         wrong_tab     click a sibling tab/link (same role + region) instead of the oracle's tab/link
         repeat        repeat the previous oracle action 2-3 times (fill/select: 1-2, MicroAgent SKIPs them)
         pingpong      after the oracle's tab/link X: sibling Y, X [, Y, X] (tab-switching loop)
         wrong_field   fill the oracle's value into a different textbox
         wrong_option  select a different option in the oracle's combobox / radiogroup
         scroll_away   scroll(down) (rejected by MicroAgent when every control is already listed)
     Recovery candidates, tried shortest first: resume (next oracle action, scrolling back if needed),
     undo (restore the wrong field's old value / re-click the wrong checkbox), nav_back (the link named
     like the page we were on), replay of the last 1-3 oracle actions, each optionally after nav_back.
  3. a candidate is KEPT only if the whole episode then passes the source's verifier with no unsafe
     codes, every injected action was emitted, and every row step was accepted by MicroAgent. So the
     recorded "correct move" provably leads to success from the real (perturbed) state.
Rows = the recovery steps + the first resumed oracle step; never the prefix (plain oracle data) and never
the injected mistake (zero loss on the wrong action). Loop rule: for every type except an explicit undo,
a row's first action may not equal the last injected action (that would teach the loop). Answer tasks:
a done(answer) row is kept only if the answer is readable in that row's own observation.

Every prompt is the one MicroAgent built at run time, so the "Your previous actions and results" block
(last 6 actions, with ok / SKIPPED / REJECTED and the page reached) shows the mistake exactly as the
model will see its own. Output rows use dataset.Example (same schema as dataset.py: messages, prompt,
completion, meta) for dataset.py's --extra-rows; meta.kind = "recovery", meta.task_kind = the task's
kind, meta.perturbation, meta.recovery, meta.stop_loop. stats (<out>.stats.json): counts per
perturbation, per family, per recovery candidate, and drop reasons.

Exam integrity: check_train_task refuses factory "ood" (and any ood family), MiniWoB holdout types and
families (click-tab* included) and MiniWoB eval seeds; assert_train_rows re-checks every written row.
No paid API is called (oracle only). Chromium needs the 3.12 venv (~/.awos/rl-venv/bin/python).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from scaffold.rl.contract import TaskSpec
from scaffold.rl.train.dataset import EvalSplitError, Example, check_split

PERTURBATIONS = ("wrong_click", "wrong_tab", "repeat", "pingpong", "wrong_field", "wrong_option", "scroll_away")
LOOP_KINDS = frozenset({"repeat", "pingpong"})
MODEL_LABEL = "oracle-recovery"
MAX_CANDIDATES = 6
NAV_ROLES = ("link", "tab")

REASONS = {     # optional templated '# reason' line on the FIRST recovery row (--reason template)
    "wrong_click": "# That click took me somewhere the goal does not need; go back on track.",
    "wrong_tab": "# That is the wrong tab for this goal; open the right one.",
    "repeat": "# Repeating the same action changed nothing; do the next step instead.",
    "pingpong": "# I keep switching between the same tabs; pick the one the goal needs and move on.",
    "wrong_field": "# I typed into the wrong field; fix it and use the right one.",
    "wrong_option": "# That option is not what the goal asks for; select the right one.",
    "scroll_away": "# Scrolling did not help; act on the control the goal needs.",
}


# ------------------------------------------------------------ integrity --

def _miniwob_holdout() -> Tuple[set, set]:
    from scaffold.rl.miniwob import catalog
    held = set(catalog.frozen_holdout())
    return held, {catalog.INCLUDED[t].family for t in held if t in catalog.INCLUDED}


def _factory_ood_families() -> set:
    from scaffold.rl.factory.app import FAMILIES
    return {name for name, f in FAMILIES.items() if getattr(f, "ood", False)}


def check_train_task(task: Any) -> None:
    """Raise EvalSplitError unless ``task`` (TaskSpec or dict) is trainable. Fails closed."""
    d = task.to_dict() if hasattr(task, "to_dict") else dict(task)
    check_split(d)
    src, fam, meta = d.get("source"), d.get("family"), d.get("meta") or {}
    if src == "miniwob":
        from scaffold.rl.miniwob import catalog
        held, held_fams = _miniwob_holdout()
        typ = meta.get("type") or ((d.get("id") or "").split("/") + ["", ""])[1]
        if typ in held or fam in held_fams or typ not in catalog.INCLUDED:
            raise EvalSplitError(f"MiniWoB type {typ!r} / family {fam!r} is holdout or unknown: never trained on")
        if int(meta.get("seed", -1)) < catalog.TRAIN_SEED_BASE:
            raise EvalSplitError(f"MiniWoB seed {meta.get('seed')} < {catalog.TRAIN_SEED_BASE} is an eval seed")
    elif src == "factory":
        if fam in _factory_ood_families():
            raise EvalSplitError(f"factory family {fam!r} is an ood family")
    else:
        raise EvalSplitError(f"source {src!r} is not a known training source")


def assert_train_rows(rows: Sequence[dict]) -> None:
    """Every written row comes from a trainable task (re-checked from the row's own meta)."""
    held, held_fams = _miniwob_holdout()
    ood = _factory_ood_families()
    for r in rows:
        m = r["meta"]
        if m.get("split") != "train" or m.get("kind") != "recovery":
            raise EvalSplitError(f"row of {m.get('task_id')} has split={m.get('split')} kind={m.get('kind')}")
        if m.get("task_source") == "miniwob":
            typ = m["task_id"].split("/")[1]
            if typ in held or m.get("family") in held_fams:
                raise EvalSplitError(f"holdout MiniWoB row {m['task_id']}")
        elif m.get("family") in ood:
            raise EvalSplitError(f"ood factory row {m['task_id']}")


# -------------------------------------------------- observation parsing --

_CTRL = re.compile(r'^\[(\d+)\] (\S+) "((?:[^"\\]|\\.)*)"(.*)$', re.M)     # == micro_agent._CTRL_LINE
_SHOWING = re.compile(r"^Controls \((\d+) total, showing (\d+)-(\d+)\):", re.M)
_PAGE = re.compile(r'^Current page: "((?:[^"\\]|\\.)*)"(?:.*?main heading "((?:[^"\\]|\\.)*)")?', re.M)


@dataclass
class Ctl:
    n: int
    role: str
    name: str
    context: str = ""
    value: Optional[str] = None
    options: List[str] = field(default_factory=list)
    dialog: bool = False

    def ref(self) -> dict:
        return {"role": self.role, "name": self.name, "context": self.context or None}


def _obs(user: str) -> str:
    return user.split("<observation", 1)[-1]


def controls(user: str) -> List[Ctl]:
    out = []
    for m in _CTRL.finditer(_obs(user)):
        rest = m[4]
        cm = re.search(r'\(in "((?:[^"\\]|\\.)*)"\)', rest)
        vm = re.search(r' = "((?:[^"\\]|\\.)*)"\s*$', rest)
        om = re.search(r" options=(\S.*?)(?: = |$)", rest)
        out.append(Ctl(int(m[1]), m[2], m[3].replace('\\"', '"'), cm.group(1) if cm else "",
                       vm.group(1).replace('\\"', '"') if vm else None,
                       om.group(1).split("|") if om else [], "(dialog)" in rest))
    return out


def window(user: str) -> Tuple[int, int, int]:
    m = _SHOWING.search(_obs(user))
    return (int(m[1]), int(m[2]), int(m[3])) if m else (0, 1, 0)


def page_of(user: str) -> Tuple[str, str]:
    m = _PAGE.search(user)
    return (m.group(1), m.group(2) or "") if m else ("", "")


def _q(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def _norm(s: str) -> str:
    return " ".join(str(s or "").split()).lower()


def _safe(c: Ctl) -> bool:
    """Plausible, non-destructive mistake target (MicroAgent's own destructive rule)."""
    try:
        from scaffold.agent.desktop.micro_agent import _DESTRUCTIVE_RE
    except Exception:                                    # pragma: no cover
        _DESTRUCTIVE_RE = None
    if not c.name.strip():
        return False
    return _DESTRUCTIVE_RE is None or not (_DESTRUCTIVE_RE.search(c.name) or _DESTRUCTIVE_RE.search(c.context))


# --------------------------------------------------- extra MiniWoB oracles --
# Train-split types with no oracle in miniwob/oracles.py. "seek" = expand the first tab not clicked yet
# until the link is visible, then click it (click-collapsible-2: accordion sections exposed as role=tab,
# the closest TRAIN analog of the holdout click-tab family).

def _collapsible_2(u: str) -> Optional[List[dict]]:
    m = re.fullmatch(r'Expand the sections below, to find and click on the link "(.+)"\.', u)
    if not m:
        return None
    return [{"op": "seek", "role": "link", "name": m.group(1), "via": "tab"},
            {"op": "done", "text": f'Clicked the link "{m.group(1)}".'}]


def _dialog_2(u: str) -> Optional[List[dict]]:
    m = re.fullmatch(r'Click the button in the dialog box labeled "(.+)"\.', u)
    return [{"op": "click", "role": "button", "name": m.group(1)}, {"op": "done", "text": f"Clicked {m.group(1)}."}] \
        if m else None


def _test_2(u: str) -> Optional[List[dict]]:
    m = re.fullmatch(r"Click button (ONE|TWO)\.", u)
    return [{"op": "click", "role": "button", "name": m.group(1)}, {"op": "done", "text": f"Clicked {m.group(1)}."}] \
        if m else None


def _enter_text_2(u: str) -> Optional[List[dict]]:
    m = re.fullmatch(r'Type "(.+)" in all (upper|lower) case letters in the text input and press Submit\.', u)
    if not m:
        return None
    v = m.group(1).upper() if m.group(2) == "upper" else m.group(1).lower()
    return [{"op": "fill", "role": "textbox", "name": "", "value": v}, {"op": "click", "role": "button", "name": "Submit"},
            {"op": "done", "text": f'Entered "{v}" and submitted.'}]


EXTRA_MINIWOB_ORACLES = {"click-collapsible-2": _collapsible_2, "click-collapsible-2-nodelay": _collapsible_2,
                         "click-dialog-2": _dialog_2, "click-test-2": _test_2, "enter-text-2": _enter_text_2}


def miniwob_oracle(source: Any, task: TaskSpec) -> List[dict]:
    steps = source.oracle(task)
    if steps:
        return steps
    fn = EXTRA_MINIWOB_ORACLES.get(task.meta.get("type", ""))
    utt = " ".join((task.meta.get("utterance") or "").split())
    return (fn(utt) or []) if fn and utt else []


def oracle_steps(source: Any, task: TaskSpec) -> List[dict]:
    return miniwob_oracle(source, task) if task.source == "miniwob" else [dict(s) for s in source.oracle(task)]


# --------------------------------------------------------------- the model --

@dataclass
class Plan:
    ptype: str = ""                      # "" = clean oracle run
    k: int = 0                           # oracle actions emitted before the injection
    reps: int = 1
    seed: int = 0
    recovery: List[dict] = field(default_factory=list)   # semantic moves (+ "__undo" / "__nav_back")
    label: str = "clean"


@dataclass
class Emitted:
    phase: str                           # prefix | perturb | recover | resume
    text: str                            # the reply
    op: str
    ref: Optional[dict] = None           # {"role","name","context"} of the resolved control
    arg: Optional[str] = None            # fill value / select option
    auto: bool = False                   # an auto-scroll serving the move


class PlanModel:
    """ActionModel: oracle prefix -> injected mistake(s) -> recovery moves -> rest of the oracle, each
    semantic move resolved against the live observation (like harness.models.OracleModel, plus scroll
    up, tabs, 'seek'). Anything it cannot resolve ends the run with needs_user (run then fails)."""
    output_format = "gbnf"

    def __init__(self, steps: Sequence[dict], plan: Optional[Plan] = None):
        self.oracle = [dict(s) for s in steps]
        self.plan = plan or Plan()
        self.oi = 0
        self.n_oracle = 0
        self.pending: List[Tuple[str, dict]] = []
        self.injected = not self.plan.ptype
        self.emitted: List[Emitted] = []
        self.info: Dict[str, Any] = {}
        self.failure = ""
        self.inapplicable = ""
        self.tried_tabs: set = set()
        self.scrolls = 0
        self.n_perturb = 0

    # -- helpers --------------------------------------------------------------
    def _fail(self, why: str) -> str:
        self.failure = self.failure or why
        self.pending, self.oi = [], len(self.oracle)
        return f"needs_user({_q(('recovery: ' + why)[:190])})"

    def _emit(self, phase: str, text: str, op: str, ctl: Optional[Ctl] = None, arg: Optional[str] = None,
              auto: bool = False) -> str:
        if ctl is not None and op == "click" and ctl.role == "tab":
            self.tried_tabs.add(_norm(ctl.name))
        self.emitted.append(Emitted(phase, text, op, ctl.ref() if ctl else None, arg, auto))
        return text

    def _find(self, user: str, m: dict) -> Optional[Ctl]:
        from scaffold.rl.harness.models import find_control
        n = find_control(user, m.get("role"), str(m.get("name", "")), m.get("context"), m.get("dialog"))
        if n is None or n < 0:
            if n is not None or m.get("context") is not None:
                # a recorded ref may carry a context that is not needed on this page; retry without it
                n2 = find_control(user, m.get("role"), str(m.get("name", "")), None, m.get("dialog"))
                if n2 is not None and n2 > 0:
                    n = n2
            if (n is None or n < 0) and not str(m.get("name", "")).strip() and m.get("role"):
                # MiniWoB oracles name an unlabeled field ""; the page reader now labels it
                # "... (unlabeled)", so fall back to the page's only control of that role
                only = [c for c in controls(user) if c.role == m["role"]]
                n = only[0].n if len(only) == 1 else None
            if n is None or n < 0:
                return None
        return next((c for c in controls(user) if c.n == n), None)

    def _resolve(self, phase: str, m: dict, user: str) -> Tuple[Optional[str], bool]:
        """-> (reply or None if unresolvable, consumed)"""
        op = m.get("op")
        if op in ("done", "needs_user"):
            return self._emit(phase, f"{op}({_q(str(m.get('text') or ''))})", op), True
        if op == "scroll":
            return self._emit(phase, f"scroll({m.get('direction', 'down')})", "scroll"), True
        if op == "__undo":
            u = self.info.get("undo")
            if not u:
                return None, True
            return self._resolve(phase, u, user)
        if op == "__nav_back":
            title, heading = self.info.get("prev_title", ""), self.info.get("prev_heading", "")
            want = {_norm(heading), _norm(title.split(" - ")[0])} - {""}
            hits = [c for c in controls(user) if c.role in NAV_ROLES and _norm(c.name) in want]
            if len(hits) != 1:
                return None, True
            c = hits[0]
            return self._emit(phase, f"click({c.n})", "click", c), True
        if op == "seek":
            c = self._find(user, {"role": m.get("role", "link"), "name": m["name"]})
            if c is not None:
                self.scrolls = 0
                return self._emit(phase, f"click({c.n})", "click", c), True
            tabs = [x for x in controls(user) if x.role == m.get("via", "tab") and _norm(x.name) not in self.tried_tabs]
            if not tabs:
                return None, True
            return self._emit(phase, f"click({tabs[0].n})", "click", tabs[0]), False
        c = self._find(user, m)
        if c is None:
            total, a, b = window(user)
            if self.scrolls < 5 and a > 1:
                self.scrolls += 1
                return self._emit(phase, "scroll(up)", "scroll", auto=True), False
            if self.scrolls < 5 and b < total:
                self.scrolls += 1
                return self._emit(phase, "scroll(down)", "scroll", auto=True), False
            return None, True
        self.scrolls = 0
        if op == "click":
            return self._emit(phase, f"click({c.n})", "click", c), True
        if op == "fill":
            from scaffold.agent.desktop.micro_agent import HANDLE_RE
            v = str(m.get("value", ""))
            return self._emit(phase, f"fill({c.n}, {v if HANDLE_RE.match(v) else _q(v)})", "fill", c, v), True
        if op == "select":
            o = str(m.get("option", ""))
            return self._emit(phase, f"select({c.n}, {_q(o)})", "select", c, o), True
        return None, True

    # -- the perturbation -------------------------------------------------------
    def _peek_target(self, user: str) -> Tuple[Optional[dict], Optional[Ctl]]:
        if self.oi >= len(self.oracle):
            return None, None
        m = self.oracle[self.oi]
        if m.get("op") == "seek":
            return m, None
        if m.get("op") in ("click", "fill", "select"):
            return m, self._find(user, m)
        return m, None

    def _last_oracle(self) -> Optional[Emitted]:
        return next((e for e in reversed(self.emitted) if e.phase in ("prefix", "resume") and not e.auto
                     and e.op in ("click", "fill", "select")), None)

    def _make_perturbation(self, user: str) -> Optional[List[dict]]:
        p, rng = self.plan, random.Random(self.plan.seed)
        self.info["prev_title"], self.info["prev_heading"] = page_of(user)
        ctls = controls(user)
        tm, tc = self._peek_target(user)
        prev = self._last_oracle()

        def move(c: Ctl, op: str = "click", **kw) -> dict:
            return {"op": op, "role": c.role, "name": c.name, "context": c.context or None, **kw}

        if p.ptype == "scroll_away":
            return [{"op": "scroll", "direction": "down"}]
        if p.ptype == "repeat":
            if prev is None or prev.ref is None:
                return None
            reps = max(1, min(p.reps, 2)) if prev.op in ("fill", "select") else max(2, min(p.reps, 3))
            m = {"op": prev.op, **prev.ref}
            if self._find(user, m) is None:
                return None                               # the same control is not on this page
            if prev.op == "fill":
                m["value"] = prev.arg
            elif prev.op == "select":
                m["option"] = prev.arg
            return [dict(m) for _ in range(reps)]
        if p.ptype == "pingpong":
            if prev is None or prev.ref is None or prev.op != "click" or prev.ref["role"] not in NAV_ROLES:
                return None
            here = self._find(user, {"role": prev.ref["role"], "name": prev.ref["name"]})   # its region may
            if here is None:                                                                # be renamed by the tab
                return None
            sib = [c for c in ctls if c.role == here.role and _norm(c.context) == _norm(here.context)
                   and c.n != here.n and abs(c.n - here.n) <= 4 and _safe(c)]
            if not sib:
                return None
            y = rng.choice(sib)
            x = {"op": "click", **here.ref()}
            # X (oracle) -> Y, X [, Y, X]: the loop ends ON the right tab, so the row is the move that
            # breaks it (ending on Y would make the next right move X, i.e. one more ping-pong step)
            return [move(y), dict(x)] * (2 if p.reps >= 3 else 1)
        if p.ptype == "wrong_tab":
            if tm is None or tm.get("op") not in ("click", "seek"):
                return None
            if tm.get("op") == "seek":
                tabs = [c for c in ctls if c.role == tm.get("via", "tab") and _safe(c)]
                untried = [c for c in tabs if _norm(c.name) not in self.tried_tabs]
                first = untried[0] if untried else None
                cands = [c for c in tabs if c is not first and (first is None or _norm(c.name) != _norm(first.name))]
            else:
                if tc is None or tc.role not in NAV_ROLES:
                    return None
                cands = [c for c in ctls if c.role == tc.role and _norm(c.context) == _norm(tc.context)
                         and c.n != tc.n and abs(c.n - tc.n) <= 4 and _safe(c)]
            if not cands:
                return None
            return [move(rng.choice(cands))]
        if p.ptype == "wrong_click":
            if tm is None:
                return None
            avoid = {tc.n} if tc is not None else set()
            roles = NAV_ROLES + (("checkbox",) if tc is not None and tc.role == "checkbox" else ())
            cands = [c for c in ctls if c.role in roles and c.n not in avoid and _safe(c) and not c.dialog
                     and (tc is None or _norm(c.name) != _norm(tc.name))]
            far = [c for c in cands if tc is None or _norm(c.context) != _norm(tc.context) or c.role == "checkbox"]
            cands = far or cands
            if not cands:
                return None
            c = rng.choice(cands)
            if c.role == "checkbox":
                self.info["undo"] = move(c)
            return [move(c)]
        if p.ptype == "wrong_field":
            if tm is None or tm.get("op") != "fill" or tc is None:
                return None
            v = str(tm.get("value", ""))
            if v.startswith("{{secret:"):
                return None                               # never put a secret into another field
            cands = [c for c in ctls if c.role == "textbox" and c.n != tc.n and c.name.strip()]
            same = [c for c in cands if _norm(c.context) == _norm(tc.context)]
            cands = same or cands
            if not cands:
                return None
            c = rng.choice(cands)
            self.info["undo"] = move(c, "fill", value=c.value or "")
            return [move(c, "fill", value=v)]
        if p.ptype == "wrong_option":
            if tm is None or tm.get("op") != "select" or tc is None:
                return None
            opts = [o for o in tc.options if _norm(o) != _norm(tm.get("option", "")) and o.strip()]
            if not opts:
                return None
            return [move(tc, "select", option=rng.choice(opts))]
        return None

    # -- ActionModel ------------------------------------------------------------
    def decide(self, system: str, user: str, grammar: Optional[str] = None) -> str:
        for _ in range(8):
            if not self.injected and self.n_oracle >= self.plan.k:
                self.injected = True
                moves = self._make_perturbation(user)
                if not moves:
                    self.inapplicable = f"{self.plan.ptype} not applicable at k={self.plan.k}"
                    return self._fail(self.inapplicable)
                self.n_perturb = len(moves)
                self.pending = [("perturb", m) for m in moves] + [("recover", m) for m in self.plan.recovery]
            if self.pending:
                phase, m = self.pending[0]
                text, consumed = self._resolve(phase, m, user)
                if text is None:
                    return self._fail(f"{phase} move {m.get('op')} {m.get('role')} {m.get('name')!r} unresolved")
                if consumed:
                    self.pending.pop(0)
                return text
            if self.oi >= len(self.oracle):
                return self._fail("script exhausted")
            phase = "resume" if self.plan.ptype and self.injected else "prefix"
            m = self.oracle[self.oi]
            text, consumed = self._resolve(phase, m, user)
            if text is None:
                return self._fail(f"oracle {m.get('op')} {m.get('role')} {m.get('name')!r} unresolved")
            if consumed:
                self.oi += 1
            if not self.emitted[-1].auto and self.emitted[-1].op in ("click", "fill", "select"):
                self.n_oracle += 1
            return text
        return self._fail("no progress")


# --------------------------------------------------------- plans + rows --

def clean_actions(model: PlanModel) -> List[Emitted]:
    """Oracle actions of a clean run (no auto-scrolls)."""
    return [e for e in model.emitted if not e.auto]


def applicable_ks(ptype: str, acts: Sequence[Emitted]) -> List[int]:
    """Injection points k (oracle actions taken before the mistake) worth trying for ``ptype``.
    acts[k] is the oracle's next move at the injection point (possibly done / needs_user)."""
    n_act = sum(1 for a in acts if a.op in ("click", "fill", "select"))
    idx = [i for i, a in enumerate(acts) if a.op in ("click", "fill", "select")]
    ks: List[int] = []
    # a run stopped by a product guard (sign-in wall ...) never asks the model for the terminal move
    last = n_act + 1 if acts and acts[-1].op in ("done", "needs_user") else n_act
    for k in range(last):
        nxt = acts[idx[k]] if k < len(idx) else None
        prv = acts[idx[k - 1]] if k >= 1 else None
        if ptype in ("wrong_click", "scroll_away"):
            ok = True
        elif ptype == "wrong_tab":
            ok = nxt is not None and nxt.op == "click" and (nxt.ref or {}).get("role") in NAV_ROLES
        elif ptype == "repeat":
            ok = prv is not None
        elif ptype == "pingpong":
            ok = prv is not None and prv.op == "click" and (prv.ref or {}).get("role") in NAV_ROLES
        elif ptype == "wrong_field":
            ok = nxt is not None and nxt.op == "fill"
        elif ptype == "wrong_option":
            ok = nxt is not None and nxt.op == "select"
        else:
            ok = False
        if ok:
            ks.append(k)
    return ks


def _ref_move(e: Emitted) -> dict:
    m = {"op": e.op, **(e.ref or {})}
    if e.op == "fill":
        m["value"] = e.arg
    elif e.op == "select":
        m["option"] = e.arg
    return m


def candidates(ptype: str, k: int, acts: Sequence[Emitted]) -> List[Tuple[str, List[dict]]]:
    """Recovery candidates, shortest first (resume = go straight to the next oracle action)."""
    idx = [i for i, a in enumerate(acts) if a.op in ("click", "fill", "select")]
    prefix = [_ref_move(acts[i]) for i in idx[:k]]
    out: List[Tuple[str, List[dict]]] = []
    if ptype == "wrong_field":
        out.append(("undo", [{"op": "__undo"}]))
    out.append(("resume", []))
    if ptype == "wrong_click":
        out.append(("undo", [{"op": "__undo"}]))
    if ptype in ("wrong_click", "wrong_tab"):
        out.append(("nav_back", [{"op": "__nav_back"}]))
    if ptype in ("wrong_click", "wrong_tab"):
        for j in range(1, 4):
            if j > len(prefix):
                break
            out.append((f"replay{j}", prefix[-j:]))
            if ptype in ("wrong_click", "wrong_tab"):
                out.append((f"nav_back+replay{j}", [{"op": "__nav_back"}] + prefix[-j:]))
    return out[:MAX_CANDIDATES]


@dataclass
class Attempt:
    ok: bool
    why: str
    rows: List[Example] = field(default_factory=list)


def extract_rows(task: TaskSpec, traj: Any, score: Any, model: PlanModel, *, reason: str = "none") -> Attempt:
    """Verified run -> recovery rows (see module doc for every rule)."""
    p = model.plan
    if model.inapplicable:
        return Attempt(False, "not_applicable")
    if not model.injected:
        return Attempt(False, "ended_before_injection")
    if getattr(traj, "error", ""):
        return Attempt(False, "infra_error")
    if model.failure:
        return Attempt(False, "unresolved")
    if not score.success:
        return Attempt(False, "not_success")
    if score.unsafe:
        return Attempt(False, "unsafe")
    if (task.kind == "pause") != (traj.outcome == "needs_user"):
        return Attempt(False, "outcome_kind_mismatch")
    steps = traj.steps
    if len(steps) != len(model.emitted):
        return Attempt(False, "step_count_mismatch")
    phases = [e.phase for e in model.emitted]
    if sum(1 for e in model.emitted if e.phase == "perturb" and not e.auto) != model.n_perturb or not model.n_perturb:
        return Attempt(False, "perturbation_not_emitted")
    pick: List[int] = [i for i, ph in enumerate(phases) if ph == "recover"]
    for i, ph in enumerate(phases):
        if ph == "resume":
            pick.append(i)
            if not model.emitted[i].auto:
                break
    if not pick:
        return Attempt(False, "no_recovery_step")
    last_bad = max(i for i, ph in enumerate(phases) if ph == "perturb")
    first = steps[pick[0]]
    if first.action == steps[last_bad].action and p.label != "undo":
        return Attempt(False, "recovery_repeats_mistake")
    rows: List[Example] = []
    for j, i in enumerate(pick):
        s = steps[i]
        if not s.valid or not s.grammar or not (s.reply or "").strip():
            return Attempt(False, "row_step_rejected")
        reply = s.reply.strip()
        if task.kind == "answer" and s.action.startswith("done("):
            # the answer must be readable in THIS prompt; a value only seen before the mistake would
            # teach answering from memory (the history block keeps titles, not page text)
            ans = model.emitted[i].text[len("done("):-1]
            try:
                ans = json.loads(ans)
            except ValueError:
                pass
            if _norm(ans) not in _norm(s.user.split("<observation", 1)[-1]):
                return Attempt(False, "answer_not_on_page")
        if reason == "template" and j == 0 and not reply.startswith("#"):
            reply = REASONS[p.ptype] + "\n" + reply
        rows.append(Example(system=s.system, user=s.user, reply=reply, meta={
            "src": "recovery", "kind": "recovery", "perturbation": p.ptype, "recovery": p.label,
            "stop_loop": p.ptype in LOOP_KINDS, "perturb_k": p.k,
            "perturb_actions": [steps[x].action for x in range(len(steps)) if phases[x] == "perturb"],
            "row_role": "recover" if phases[i] == "recover" else "resume",
            "task_id": task.id, "task_source": task.source, "family": task.family, "task_kind": task.kind,
            "split": task.split, "sample": 0, "step": int(s.i), "model": MODEL_LABEL, "action": s.action,
            "reason_line": reason == "template" and j == 0}))
    return Attempt(True, "kept", rows)


def plan_seed(task_id: str, ptype: str, k: int, seed: int) -> int:
    return int.from_bytes(hashlib.sha256(f"{seed}:{task_id}:{ptype}:{k}".encode()).digest()[:6], "big")


# --------------------------------------------------------------- driving --

def run_plan(source: Any, task: TaskSpec, steps: Sequence[dict], plan: Plan, chromium: Any):
    from scaffold.rl.harness.episode import run_episode
    model = PlanModel(steps, plan)
    traj, score = run_episode(source, task, model, model_label=MODEL_LABEL, guards="product", chromium=chromium)
    return traj, score, model


def generate_task(source: Any, task: TaskSpec, chromium: Any, *, types: Sequence[str] = PERTURBATIONS,
                  per_type: int = 1, seed: int = 0, reason: str = "none") -> Tuple[List[Example], Counter]:
    """All recovery rows for one task, plus counters ("<ptype>:<outcome>", "episodes", ...)."""
    check_train_task(task)
    st: Counter = Counter()
    steps = oracle_steps(source, task)
    if not steps:
        st["task:no_oracle"] += 1
        return [], st
    traj, score, clean = run_plan(source, task, steps, Plan(), chromium)
    st["episodes"] += 1
    if traj.error or clean.failure or not score.success or score.unsafe:
        st["task:clean_oracle_failed"] += 1
        return [], st
    st["task:used"] += 1
    acts = clean_actions(clean)
    rows: List[Example] = []
    for ptype in types:
        ks = applicable_ks(ptype, acts)
        if not ks:
            st[f"{ptype}:no_injection_point"] += 1
            continue
        rng = random.Random(plan_seed(task.id, ptype, -1, seed))
        kept = tries = 0
        for k in rng.sample(ks, len(ks)):            # an injection point that turns out not applicable at
            if kept >= per_type or tries >= per_type + 2:    # run time (e.g. the control to repeat is gone
                break                                  # from the new page) gives way to the next one
            tries += 1
            reps = 2 + rng.random().__lt__(0.5)
            got = None
            whys: List[str] = []
            for label, rec in candidates(ptype, k, acts):
                plan = Plan(ptype=ptype, k=k, reps=reps, seed=plan_seed(task.id, ptype, k, seed), recovery=rec,
                            label=label)
                traj, score, model = run_plan(source, task, steps, plan, chromium)
                st["episodes"] += 1
                att = extract_rows(task, traj, score, model, reason=reason)
                whys.append(att.why)
                if att.ok:
                    got = att
                    break
                if att.why in ("not_applicable", "ended_before_injection"):
                    break
            if whys and whys[0] in ("not_applicable", "ended_before_injection"):
                st[f"{ptype}:{whys[0]}"] += 1
                continue
            st[f"{ptype}:plans"] += 1
            if got is None:
                st[f"{ptype}:dropped:{whys[-1] if whys else 'no_candidate'}"] += 1
                continue
            kept += 1
            st[f"{ptype}:kept"] += 1
            st[f"{ptype}:recovery:{got.rows[0].meta['recovery']}"] += 1
            rows.extend(got.rows)
    return rows, st


# ------------------------------------------------------------ task lists --

def _seeds(s: str) -> List[int]:
    out: List[int] = []
    for part in s.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def miniwob_train_types() -> List[str]:
    """Train-split MiniWoB types that have an oracle (miniwob.oracles or EXTRA_MINIWOB_ORACLES)."""
    from scaffold.rl.miniwob import catalog
    from scaffold.rl.miniwob.oracles import ORACLES
    held, held_fams = _miniwob_holdout()
    return sorted(t for t in set(ORACLES) | set(EXTRA_MINIWOB_ORACLES)
                  if t in catalog.INCLUDED and t not in held and catalog.INCLUDED[t].family not in held_fams)


def make_source(name: str, cfg: dict) -> Any:
    if name == "factory":
        from scaffold.rl.factory import FactorySource
        return FactorySource()
    if name == "miniwob":
        from scaffold.rl.miniwob.source import MiniWobSource
        return MiniWobSource(seeds=cfg["miniwob_seeds"], types=cfg["miniwob_types"],
                             utterances=cfg.get("utterances") or {})
    raise SystemExit(f"unknown source {name!r}")


def list_tasks(source_name: str, src: Any, cfg: dict) -> List[TaskSpec]:
    fams = set(cfg.get("families") or [])
    out = []
    for t in src.tasks("train"):
        if fams and t.family not in fams and (t.meta or {}).get("type") not in fams:
            continue
        if source_name == "miniwob" and not t.meta.get("utterance"):
            continue                                    # no exact goal for this seed: skip, never guess
        check_train_task(t)
        out.append(t)
    return out


def round_robin(tasks: Sequence[TaskSpec], n: int) -> List[TaskSpec]:
    """First ``n`` tasks taken one template (factory family/template, MiniWoB type) at a time, so smoke
    runs cover every family."""
    groups: Dict[str, List[TaskSpec]] = {}
    for t in tasks:
        groups.setdefault((t.meta or {}).get("type") or "/".join(t.id.split("/")[1:3]), []).append(t)
    out: List[TaskSpec] = []
    while len(out) < n and any(groups.values()):
        for g in list(groups):
            if groups[g] and len(out) < n:
                out.append(groups[g].pop(0))
    return out


_W: Dict[str, Any] = {}


def _worker_init(cfg: dict) -> None:
    from scaffold.rl.harness.chromium import Chromium
    _W["cfg"] = cfg
    _W["chromium"] = Chromium()
    _W["sources"] = {}


def _worker_job(job: Tuple[str, str]) -> Tuple[List[dict], Dict[str, int], str, str]:
    name, tid = job
    cfg = _W["cfg"]
    if name not in _W["sources"]:
        src = make_source(name, cfg)
        src.start()
        _W["sources"][name] = (src, {t.id: t for t in src.tasks("train")})
    src, by_id = _W["sources"][name]
    task = by_id[tid]
    rows, st = generate_task(src, task, _W["chromium"], types=cfg["types"], per_type=cfg["per_type"],
                             seed=cfg["seed"], reason=cfg["reason"])
    return [r.to_json() for r in rows], dict(st), f"{task.source}/{task.family}", tid


def summarize(rows: Sequence[dict], counters: Counter, fam_counters: Dict[str, Counter]) -> dict:
    per_p, per_f, per_r, per_role = Counter(), Counter(), Counter(), Counter()
    for r in rows:
        m = r["meta"]
        per_p[m["perturbation"]] += 1
        per_f[f"{m['task_source']}/{m['family']}"] += 1
        per_r[f"{m['perturbation']}/{m['recovery']}"] += 1
        per_role[m["row_role"]] += 1
    perturb = {}
    for p in PERTURBATIONS:
        perturb[p] = {"plans": counters.get(f"{p}:plans", 0), "kept": counters.get(f"{p}:kept", 0), "rows": per_p.get(p, 0),
                      "dropped": {k.split(":", 2)[2]: v for k, v in counters.items() if k.startswith(f"{p}:dropped:")},
                      "no_injection_point": counters.get(f"{p}:no_injection_point", 0),
                      "not_applicable": counters.get(f"{p}:not_applicable", 0),
                      "ended_before_injection": counters.get(f"{p}:ended_before_injection", 0)}
    return {"rows": len(rows), "stop_loop_rows": sum(1 for r in rows if r["meta"]["stop_loop"]),
            "episodes": counters.get("episodes", 0),
            "tasks": {k.split(":", 1)[1]: v for k, v in counters.items() if k.startswith("task:")},
            "per_perturbation": perturb, "rows_per_family": dict(per_f.most_common()),
            "plans_kept_per_family": {f: dict(c) for f, c in sorted(fam_counters.items())},
            "rows_per_recovery": dict(per_r.most_common()), "rows_per_role": dict(per_role)}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scaffold.rl.train.recovery", description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, type=Path, help="rows JSONL (stats go to <out>.stats.json)")
    ap.add_argument("--sources", default="factory,miniwob")
    ap.add_argument("--types", default=",".join(PERTURBATIONS))
    ap.add_argument("--per-type", type=int, default=1, help="injection points per (task, perturbation)")
    ap.add_argument("--families", default="", help="comma list of factory families / MiniWoB types to keep")
    ap.add_argument("--miniwob-seeds", default="1000-1009", help=">= TRAIN_SEED_BASE only")
    ap.add_argument("--limit", type=int, default=0, help="first N tasks per source (smoke runs)")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--reason", choices=["none", "template"], default="none",
                    help="template: a fixed '# reason' line on the first recovery row of each plan")
    ap.add_argument("--dry-run", action="store_true", help="list the tasks that would be used; no browser")
    a = ap.parse_args(argv)
    types = [t for t in a.types.split(",") if t]
    bad = [t for t in types if t not in PERTURBATIONS]
    if bad:
        ap.error(f"unknown perturbation(s) {bad}; known: {PERTURBATIONS}")
    seeds = _seeds(a.miniwob_seeds)
    from scaffold.rl.miniwob import catalog
    if any(s < catalog.TRAIN_SEED_BASE for s in seeds):
        print(f"REFUSING: MiniWoB seeds below {catalog.TRAIN_SEED_BASE} are eval seeds", file=sys.stderr)
        return 2
    cfg: Dict[str, Any] = {"types": types, "per_type": a.per_type, "seed": a.seed, "reason": a.reason,
                           "families": [f for f in a.families.split(",") if f], "miniwob_seeds": seeds,
                           "miniwob_types": miniwob_train_types()}
    if cfg["families"]:
        keep = [t for t in cfg["miniwob_types"] if t in cfg["families"]]
        cfg["miniwob_types"] = keep
    sources = [s for s in a.sources.split(",") if s]
    if "miniwob" in sources and not cfg["miniwob_types"]:
        sources.remove("miniwob")
    if "miniwob" in sources and not a.dry_run:
        from scaffold.rl.miniwob.source import load_utterances
        utt = load_utterances()
        need = [(t, s) for t in cfg["miniwob_types"] for s in seeds if f"{t}/{s}" not in utt]
        if need:
            from scaffold.rl.miniwob.render import compute_utterances
            print(f"rendering {len(need)} MiniWoB train-seed utterances (local Chromium)...", flush=True)
            utt.update(compute_utterances(sorted({t for t, _ in need}), sorted({s for _, s in need})))
        cfg["utterances"] = {k: v for k, v in utt.items() if int(k.rsplit("/", 1)[1]) >= catalog.TRAIN_SEED_BASE}
    jobs: List[Tuple[str, str]] = []
    try:
        for name in sources:
            src = make_source(name, {**cfg, "utterances": cfg.get("utterances") or
                                     ({f"{t}/{s}": "dry-run" for t in cfg["miniwob_types"] for s in seeds}
                                      if a.dry_run else {})})
            ts = list_tasks(name, src, cfg)
            if a.limit:
                ts = round_robin(ts, a.limit)
            jobs += [(name, t.id) for t in ts]
            print(f"[{name}] {len(ts)} train tasks" + (f" types={cfg['miniwob_types']}" if name == "miniwob" else ""))
    except EvalSplitError as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 2
    if a.dry_run:
        print(json.dumps({"jobs": len(jobs), "types": types, "per_type": a.per_type,
                          "max_episodes": len(jobs) * (1 + len(types) * a.per_type * MAX_CANDIDATES)}, indent=1))
        return 0
    rows: List[dict] = []
    counters: Counter = Counter()
    fam: Dict[str, Counter] = {}
    seen: set = set()
    t0 = time.time()

    def take(res, i):
        rs, st, famkey, tid = res
        counters.update(st)
        fc = fam.setdefault(famkey, Counter())
        for k, v in st.items():
            if k.endswith(":kept"):
                fc[k.split(":")[0]] += v
        for r in rs:
            key = hashlib.sha256(json.dumps([r["messages"][0]["content"], r["messages"][1]["content"],
                                             r["messages"][2]["content"]]).encode()).digest()
            if key in seen:
                counters["rows_duplicate"] += 1
                continue
            seen.add(key)
            rows.append(r)
        if i % 10 == 0 or i == len(jobs):
            print(f"  {i}/{len(jobs)} tasks  rows={len(rows)}  episodes={counters['episodes']}  "
                  f"{time.time() - t0:.0f}s", flush=True)

    if a.workers <= 1:
        _worker_init(cfg)
        try:
            for i, job in enumerate(jobs, 1):
                take(_worker_job(job), i)
        finally:
            for src, _ in _W["sources"].values():
                src.stop()
            _W["chromium"].close()
    else:
        import multiprocessing as mp
        with mp.get_context("spawn").Pool(a.workers, initializer=_worker_init, initargs=(cfg,)) as pool:
            for i, res in enumerate(pool.imap_unordered(_worker_job, jobs), 1):
                take(res, i)
    assert_train_rows(rows)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    stats = summarize(rows, counters, fam)
    stats.update({"seconds": round(time.time() - t0, 1), "types": types, "per_type": a.per_type, "seed": a.seed,
                  "reason": a.reason, "miniwob_types": cfg["miniwob_types"], "miniwob_seeds": seeds,
                  "duplicates_dropped": counters.get("rows_duplicate", 0)})
    Path(str(a.out) + ".stats.json").write_text(json.dumps(stats, indent=2) + "\n")
    print(json.dumps({k: stats[k] for k in ("rows", "stop_loop_rows", "episodes", "tasks", "rows_per_family")}, indent=1))
    for p, d in stats["per_perturbation"].items():
        print(f"  {p:13s} plans={d['plans']:4d} kept={d['kept']:4d} rows={d['rows']:4d} dropped={d['dropped']}")
    print(f"-> {a.out} (+ .stats.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
