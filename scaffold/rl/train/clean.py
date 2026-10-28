"""
clean.py — training-data cleaner for the SFT builder (NEXT_WEEK_PLAN item 0.4).

Research (sft_recipes.md §1b): filtered data beats more data; scripted walkthroughs are clean but
low-diversity, so cap their share. dataset.py --clean applies these filters, in this order, and
stats.json["clean"] reports counts before/after each one:

  1. verified_success  episodes: score.success, no unsafe codes, no infra error (dataset.episode_drop_reason;
                       always on, reported here so the table is complete)
  2. no_loops          episodes: drop an episode in which the same (observation, action) pair occurs more
                       than ``max_repeats`` times. The observation is the <observation> block with the
                       per-instance path id (/t/i3/) normalised; the action is the parsed action line.
                       An A->B->A->B tab loop repeats (obs A, click B), so it is caught too. A success
                       that looped still teaches looping: the whole episode goes.
  3. near_dedupe       rows: one row per (system, normalised user, action line). The user text is
                       normalised by dropping "Step N of M." and the instance id and collapsing white
                       space; the reason line is ignored (same move, different wording = duplicate).
  4. scripted_cap      train rows: rows from scripted models (oracle / scripted) are downsampled to at
                       most ``scripted_frac`` (default 0.30) of the train set. Kept scripted rows are
                       chosen family-first where model rows are scarcest (oracle rows fill coverage gaps),
                       seeded. If there are no model rows the cap is skipped and reported, not applied
                       (it would delete everything).

Exam integrity (exam_reason): the exam is factory split "ood" (whole families with ood=True) and the
MiniWoB holdout task types (data/holdout.json, by family). A row or episode whose split is not "train",
or whose factory family is an ood family, or whose MiniWoB type/family is held out, is refused. The
family lists are read from the factory registry and the frozen holdout file, so a new ood family is
covered without editing this file.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from functools import lru_cache
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple

#: model labels whose rows are scripted walkthroughs (harness runner --model oracle|scripted)
SCRIPTED_MODELS = ("oracle", "scripted")
DEFAULT_SCRIPTED_FRAC = 0.30
DEFAULT_MAX_REPEATS = 1

_OBS = re.compile(r'<observation untrusted="true">\n?(.*?)\n?</observation>', re.S)
_INSTANCE = re.compile(r"/t/i\d+/")
_STEP = re.compile(r"^Step \d+ of \d+\.\s*$", re.M)
_WS = re.compile(r"\s+")


# ------------------------------------------------------------ exam guard --

@lru_cache(maxsize=1)
def exam_sets() -> Tuple[FrozenSet[str], FrozenSet[str], FrozenSet[str]]:
    """-> (factory ood families, miniwob holdout types, miniwob holdout families). Fails closed:
    an import error here aborts the build rather than skipping the check."""
    import scaffold.rl.factory.families  # noqa: F401,PLC0415 - registers every Family
    from scaffold.rl.factory.app import FAMILIES  # noqa: PLC0415
    from scaffold.rl.miniwob import catalog  # noqa: PLC0415
    ood = frozenset(n for n, f in FAMILIES.items() if getattr(f, "ood", False))
    held = frozenset(catalog.frozen_holdout())
    held_fams = frozenset(t.family for n, t in catalog.INCLUDED.items() if n in held)
    return ood, held, held_fams


def exam_reason(task_id: str, split: Optional[str], source: Optional[str] = None,
                family: Optional[str] = None) -> Optional[str]:
    """None if the task may be trained on; else why it is part of the exam (or unknown)."""
    if split != "train":
        return f"split={split!r}"
    ood, held, held_fams = exam_sets()
    parts = (task_id or "").split("/")
    src = source or (parts[0] if parts else "")
    if src == "factory" or parts[0] == "factory":
        fams = {family, parts[1] if len(parts) > 1 else None}
        if fams & ood:
            return f"factory ood family {sorted(f for f in fams & ood)}"
    if src == "miniwob" or parts[0] == "miniwob":
        typ = parts[1] if len(parts) > 1 else None
        if typ in held or family in held or family in held_fams or typ in held_fams:
            return f"miniwob holdout type/family {typ or family!r}"
    return None


# ------------------------------------------------------------- utilities --

def is_scripted(model: Optional[str]) -> bool:
    m = (model or "").lower()
    return any(m == s or m.startswith(s + "-") or m.startswith(s + ":") for s in SCRIPTED_MODELS)


def action_line(reply: str) -> str:
    """The action line of a reply (the last non-'#' line), stripped."""
    lines = [ln.strip() for ln in (reply or "").strip().splitlines() if ln.strip()]
    acts = [ln for ln in lines if not ln.startswith("#")]
    return acts[-1] if acts else ""


def observation_key(user: str) -> str:
    m = _OBS.search(user or "")
    obs = m.group(1) if m else (user or "")
    return _WS.sub(" ", _INSTANCE.sub("/t/i#/", obs)).strip()


def near_key(system: str, user: str, reply: str) -> bytes:
    u = _WS.sub(" ", _INSTANCE.sub("/t/i#/", _STEP.sub("", user or ""))).strip()
    return hashlib.sha256(json.dumps([(system or "").strip(), u, action_line(reply)]).encode()).digest()


def loop_reason(steps: Sequence[dict], max_repeats: int = DEFAULT_MAX_REPEATS) -> Optional[str]:
    """'loop' if some (observation, action) pair occurs more than max_repeats times among the steps."""
    seen: Counter = Counter()
    for s in steps:
        act = (s.get("action") or action_line(s.get("reply") or "")).strip()
        if not act:
            continue
        k = (observation_key(s.get("user") or ""), act)
        seen[k] += 1
        if seen[k] > max_repeats:
            return "looping"
    return None


# ----------------------------------------------------------- scripted cap --

def cap_scripted(rows: List[Any], frac: float = DEFAULT_SCRIPTED_FRAC, seed: int = 0,
                 meta=lambda r: r.meta) -> Tuple[List[Any], Dict[str, Any]]:
    """Downsample scripted rows to <= frac of the result. ``rows`` are Examples (or anything ``meta``
    maps to a dict with "model" and "family"). Order of the kept rows is preserved."""
    scripted = [i for i, r in enumerate(rows) if is_scripted(meta(r).get("model"))]
    n_model = len(rows) - len(scripted)
    rep: Dict[str, Any] = {"rows_in": len(rows), "scripted_in": len(scripted), "frac": frac}
    if not scripted or frac >= 1:
        rep.update(rows_out=len(rows), scripted_out=len(scripted), skipped="nothing to cap" if not scripted else "frac>=1")
        return rows, rep
    if n_model == 0:
        rep.update(rows_out=len(rows), scripted_out=len(scripted),
                   skipped="no model rows: cap would remove every row; applied nothing")
        return rows, rep
    allowed = int(frac / (1 - frac) * n_model) if frac > 0 else 0
    if len(scripted) <= allowed:
        rep.update(rows_out=len(rows), scripted_out=len(scripted))
        return rows, rep
    model_by_fam = Counter(meta(r).get("family") for r in rows if not is_scripted(meta(r).get("model")))
    rng = random.Random(seed)
    by_fam: Dict[Any, List[int]] = defaultdict(list)
    for i in scripted:
        by_fam[meta(rows[i]).get("family")].append(i)
    for v in by_fam.values():
        rng.shuffle(v)
    # round-robin over families, scarcest model coverage first, so the cap trims the families the
    # model data already covers and keeps oracle rows where they are the only examples
    order = sorted(by_fam, key=lambda f: (model_by_fam.get(f, 0), str(f)))
    keep: set = set()
    while len(keep) < allowed:
        progressed = False
        for f in order:
            if by_fam[f] and len(keep) < allowed:
                keep.add(by_fam[f].pop())
                progressed = True
        if not progressed:
            break
    drop = set(scripted) - keep
    out = [r for i, r in enumerate(rows) if i not in drop]
    rep.update(rows_out=len(out), scripted_out=len(keep),
               frac_out=round(len(keep) / len(out), 4) if out else 0.0)
    return out, rep


# ---------------------------------------------------------------- report --

class CleanReport:
    """Before/after counts per filter, in pipeline order (written to stats.json["clean"])."""

    def __init__(self) -> None:
        self.counts: Dict[str, Dict[str, int]] = {}
        self.extra: Dict[str, Any] = {}

    def add(self, name: str, n_in: int, n_out: int, unit: str) -> None:
        c = self.counts.setdefault(name, {"unit": unit, "in": 0, "out": 0})
        c["in"] += n_in
        c["out"] += n_out

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {k: dict(v, dropped=v["in"] - v["out"]) for k, v in self.counts.items()}
        d.update(self.extra)
        return d
