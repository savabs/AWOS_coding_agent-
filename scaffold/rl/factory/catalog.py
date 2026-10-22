"""
catalog.py — seeds -> AppSpec + TaskSpec, the oracle simulation and admission.

Every task id is ``factory/<family>/<template>/<k:03d>``; its seed is a hash of the id,
so ids and content are stable across runs and machines. Knobs drawn per task:
theme, layout, ARIA level, popup (cookie banner / newsletter), distractor count.

Splits: a family with ``ood = True`` is entirely in the frozen ``ood`` split and is
rendered only with OOD_THEMES; train families only ever use TRAIN_THEMES.

Admission: each draft's oracle intents are executed in-process through the HTML
driver (the same requests the page would issue); the task is admitted only if the
resulting state scores success with no unsafe codes. The resulting semantic steps
are what ``FactorySource.oracle`` returns.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ..contract import Step, TaskSpec, Trajectory
from . import families as _families  # noqa: F401  (registers every family)
from .app import FAMILIES, App, AppSpec, Draft
from .html import ARIA_LEVELS, LAYOUTS, OOD_THEMES, TRAIN_THEMES
from .htmldriver import SimBrowser, StepError, semantic_step
from .server import Registry, inproc_transport
from .verify import score

VERSION = "v0"
ARIA_WEIGHTS = (0.4, 0.15, 0.15, 0.15, 0.15)


def seed_of(task_id: str) -> int:
    return int(hashlib.sha256(f"{VERSION}:{task_id}".encode()).hexdigest()[:12], 16)


def draw_knobs(rng: random.Random, ood: bool) -> dict:
    return {
        "theme": rng.choice(OOD_THEMES if ood else TRAIN_THEMES),
        "layout": rng.choice(LAYOUTS),
        "aria": rng.choices(ARIA_LEVELS, weights=ARIA_WEIGHTS)[0],
        "popup": rng.choices([None, "cookie", "newsletter"], weights=(0.84, 0.08, 0.08))[0],
        "distractors": rng.randint(0, 3),
    }


def show(step: dict) -> str:
    """The step rendered as MicroAction.show() would (for oracle trajectories)."""
    op = step["op"]
    if op in ("done", "needs_user"):
        return f"{op}({json.dumps(step.get('text', ''))})"
    return f"{op}(?)"


@dataclass
class Entry:
    task: TaskSpec
    spec: AppSpec
    draft: Draft
    steps: List[dict]


try:                                           # the product's own code guards (MicroAgent)
    from scaffold.agent.desktop.micro_agent import _CAPTCHA_RE, _DESTRUCTIVE_RE, _MFA_RE
    from scaffold.agent.desktop.web_tools import _SECRETISH
except Exception:                              # pragma: no cover - product package unavailable
    _CAPTCHA_RE = _DESTRUCTIVE_RE = _MFA_RE = _SECRETISH = None

MAX_ORACLE_STEPS = 12                          # MicroAgent.MAX_STEPS: the harness caps every episode


def product_guard(view, c=None, pause: bool = False) -> Optional[str]:
    """Why MicroAgent (guards="product") would refuse this page / click, else None.
    Mirrors micro_agent.is_destructive (name OR context) and guard_page (captcha / MFA)."""
    if _DESTRUCTIVE_RE is not None and c is not None and c.role in ("button", "link") and \
            (_DESTRUCTIVE_RE.search(c.name) or _DESTRUCTIVE_RE.search(c.context)):
        return f"product guard refuses destructive control {c.name!r} (context {c.context!r})"
    if not pause and _CAPTCHA_RE is not None:
        if _CAPTCHA_RE.search(view.title + " " + view.text[:2000]):
            return "product guard: page looks like a CAPTCHA"
        if _MFA_RE.search(view.title) or any(_MFA_RE.search(x.name) for x in view.controls
                                             if x.role in ("textbox", "password")):
            return "product guard: page looks like an MFA prompt"
        if any(x.role == "password" for x in view.controls):
            return "product guard: page asks for a password"
    return None


def simulate(spec: AppSpec, intents: List[tuple], pause: bool = False) -> Tuple[List[dict], App, List[str]]:
    """Run oracle intents in-process. Returns (semantic steps, final app, status texts
    seen after the last write)."""
    reg = Registry()
    app = App(spec, iid="sim")
    reg.add(app)
    br = SimBrowser(inproc_transport(reg))
    br.open(f"http://127.0.0.1:0/t/sim/{spec.start_view}")
    steps: List[dict] = []
    last_status: List[str] = []
    for it in intents:
        op = it[0]
        why = product_guard(br.view, pause=pause)
        if why:
            raise StepError(why)
        if op in ("needs_user", "done"):
            steps.append({"op": op, "text": it[1]})
            continue
        c = br.by_ref(it[1])
        why = product_guard(br.view, c, pause=pause)
        if why and not pause:
            raise StepError(why)
        n_ev = len(app.events)
        if op == "click":
            steps.append(semantic_step(br.view, c, "click"))
            br.click(c)
        elif op == "fill":
            if _SECRETISH is not None and _SECRETISH.search(it[2]):
                raise StepError(f"fill value {it[2]!r} looks like a credential to the product (masked, refused)")
            steps.append(semantic_step(br.view, c, "fill", value=it[2]))
            br.fill(c, it[2])
        elif op == "select":
            steps.append(semantic_step(br.view, c, "select", option=it[2]))
            br.select(c, it[2])
        else:
            raise StepError(f"bad intent {it}")
        if br.status >= 400:
            raise StepError(f"HTTP {br.status} after {it}")
        if len(app.events) > n_ev:
            last_status = list(br.view.statuses)
    if len(steps) > MAX_ORACLE_STEPS:
        raise StepError(f"oracle needs {len(steps)} steps > the harness cap {MAX_ORACLE_STEPS}")
    return steps, app, last_status


def oracle_traj(task_id: str, steps: List[dict]) -> Trajectory:
    tr = Trajectory(task_id=task_id, model="oracle")
    for i, s in enumerate(steps, 1):
        tr.steps.append(Step(i=i, system="", user="", grammar=None, reply="", action=show(s)))
    last = steps[-1]["op"] if steps else ""
    tr.outcome = "needs_user" if last == "needs_user" else "verified"
    return tr


class Catalog:
    def __init__(self, families: Optional[List[str]] = None):
        self.entries: Dict[str, Entry] = {}
        self.rejected: Dict[str, str] = {}
        self.generated = 0
        for fname in sorted(FAMILIES):
            if families and fname not in families:
                continue
            fam = FAMILIES[fname]
            for tmpl in fam.templates():
                for k in range(tmpl.count):
                    tid = f"factory/{fname}/{tmpl.name}/{k:03d}"
                    self.generated += 1
                    try:
                        self._build(tid, fam, tmpl)
                    except Exception as e:          # a broken template must not kill the catalog
                        self.rejected[tid] = f"{type(e).__name__}: {e}"

    def _build(self, tid: str, fam, tmpl) -> None:
        seed = seed_of(tid)
        rng = random.Random(seed)
        knobs = draw_knobs(rng, fam.ood)
        d: Draft = tmpl.fn(random.Random(seed ^ 0x5EED), knobs)
        popup = None if (d.no_popup or d.kind == "pause") else knobs["popup"]
        spec = AppSpec(family=fam.name, seed=seed, title=d.title or fam.name.replace("_", " ").title(),
                       domain=d.domain, data=d.data, params=d.params, start_view=d.start_view,
                       theme=knobs["theme"], layout=knobs["layout"], aria=knobs["aria"], popup=popup,
                       distractors=knobs["distractors"], traps=dict(d.traps), injection_ctls=list(d.injection_ctls),
                       injection_values=list(d.injection_values), checkpoint_views=list(d.checkpoint_views))
        spec = AppSpec.from_dict(json.loads(json.dumps(spec.to_dict())))       # must be JSON-serialisable
        intents = list(d.intents)
        if popup:
            intents.insert(0, ("click", "__cookie_reject" if popup == "cookie" else "__news_close"))
        if d.kind == "act" and not any(i[0] == "done" for i in intents):
            intents.append(("done", "Done."))
        if d.kind == "answer" and _SECRETISH is not None and _SECRETISH.search(d.answer):
            raise StepError(f"answer {d.answer!r} would be masked by the product's done() logging")
        steps, app, last_status = simulate(spec, intents, pause=d.kind == "pause")
        tags = list(dict.fromkeys(d.tags))
        if spec.aria != "good":
            tags.append("aria_poor")
        if popup:
            tags.append("popup")
        meta = {"seed": seed, "template": tmpl.name, "theme": spec.theme, "layout": spec.layout,
                "aria": spec.aria, "domain": spec.domain, "popup": popup, "distractors": spec.distractors,
                "group": fam.group, "components": list(fam.components), "expect": d.expect,
                "answer": d.answer, "answer_alts": d.answer_alts,
                "ack": [re.escape(s) for s in last_status] if d.kind == "act" else ([re.escape(d.answer)] if d.kind == "answer" else []),
                "app_spec": spec.to_dict()}
        task = TaskSpec(id=tid, source="factory", family=fam.name, split="ood" if fam.ood else "train",
                        goal=d.goal, start="/" + spec.start_view, kind=d.kind, tags=tags,
                        allow_writes=True, max_steps=max(d.max_steps, len(steps) + 4), meta=meta)
        sc = score(meta, d.kind, spec.data, app.state, app.events, oracle_traj(tid, steps), spec.injection_values)
        if not sc.success:
            raise StepError(f"oracle does not reach success: {sc.notes}")
        self.entries[tid] = Entry(task, spec, d, steps)

    def tasks(self, split: Optional[str] = None) -> List[TaskSpec]:
        return [e.task for e in self.entries.values() if split is None or e.task.split == split]
