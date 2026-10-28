"""
repair_data.py — SFT rows for the repair model: "which shortlist candidate is the step's control, or
NOT_FOUND" (scaffold/agent/desktop/repair; benchmark scripts/repair_bench.py).

    python -m scaffold.rl.train.repair_data --out repair_sft [--families contacts prefs records mail]
        [--seeds 4] [--val-frac 0.1] [--max-per-kind 0]

Why: base Qwen3.5 2B on the repair benchmark (2026-10-24, rl-repair-verify run 40113478498) finds moved
or renamed controls (96-100%) but almost never answers NOT_FOUND when the control was deleted: wrong
repairs 23.3% overall (57.6% on delete, 95.6% on delete + a look-alike decoy) against a <= 2% target.

Rows are exactly what the model sees at run time: every recorded oracle step of the given app-factory
families is mutated (repair.mutate.MUTATIONS, several seeds), the strict grounder and the rule tier
run as in the product, and only when the rule tier hands a shortlist to the model is a row written:
system = repair.model.SYSTEM, user = repair.model.prompt(fingerprint, shortlist), reply =
"PICK <n>: <reason>" (the ground-truth control is in the shortlist) or "NOT_FOUND: <reason>" (deleted,
or the true control did not make the shortlist: the model cannot pick it, so abstaining is right).

Honesty: the default families are the benchmark's DEV families. Its TEST families (docs_hub, assign,
shop, checkpoints) stay unseen and are refused here, as are the factory's ood (exam) families.
Validation is split by task id. Rendered with dataset.Example, so kaggle/rl-sft/train.py takes the rows
as they are.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scaffold.agent.desktop.repair import mutate as M  # noqa: E402
from scaffold.agent.desktop.repair.core import Repairer  # noqa: E402
from scaffold.agent.desktop.repair.model import NOT_FOUND, SYSTEM, Pick, prompt  # noqa: E402
from scaffold.rl.train.dataset import Example  # noqa: E402


def _bench():
    """scripts/repair_bench.py (case collection + the product's strict grounder), loaded by path."""
    spec = importlib.util.spec_from_file_location("repair_bench", ROOT / "scripts" / "repair_bench.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


B = _bench()
DEFAULT_FAMILIES = B.DEV
HELD_OUT = set(B.TEST) | set(B.EXAM_FAMILIES)


def reason(kind_ok: bool, fp, cand=None) -> str:
    """Short deterministic evidence line (no LLM)."""
    if kind_ok and cand is not None:
        return f"same {fp.role} for the same item ({cand.role} {cand.name!r})"[:160]
    return f"the step's {fp.role} {fp.name!r} is not on the page; the candidates are other controls"[:160]


def rows_for_case(case: dict, seeds: int) -> List[Tuple[Example, str]]:
    """(example, mutation kind) for every mutation x seed of one case where the model would be asked."""
    from scaffold.rl.factory.htmldriver import parse
    v0 = parse(case["html"])
    fp = M.ctl_fingerprint(v0, case["index"])
    out = []
    for kind in M.MUTATIONS:
        for s in range(seeds):
            rng = random.Random(f"train:{s}:{case['task']}:{case['step']}:{case['index']}:{kind}")
            m = M.mutate(case["html"], case["index"], kind, rng)
            if m is None:
                continue
            cands = M.view_candidates(m.view)
            if B.strict_pick(fp, m.view) is not None:
                continue                                  # the strict grounder finds it: no model call
            seen: Dict[str, list] = {}

            def capture(f, shortlist, _seen=seen):
                _seen["short"] = list(shortlist)
                return Pick(None, "capture", calls=1)
            Repairer(capture).refind(fp, cands)
            short = seen.get("short")
            if not short:
                continue                                  # the rule tier decided alone
            gt = cands[m.gt] if m.gt is not None else None
            hit = next((i for i, c in enumerate(short) if gt is not None and (c.ref is gt.ref or c is gt)), None)
            if hit is None:
                reply = f"{NOT_FOUND}: {reason(False, fp)}"
            else:
                reply = f"PICK {hit + 1}: {reason(True, fp, short[hit])}"
            ex = Example(system=SYSTEM, user=prompt(fp, short), reply=reply,
                         meta={"task": case["task"], "family": case["family"], "split": case["split"],
                               "mutation": kind, "seed": s, "label": "NOT_FOUND" if hit is None else "PICK",
                               "kind": "repair"})
            out.append((ex, kind))
    return out


def build(families, *, seeds: int = 4, val_frac: float = 0.1, max_per_kind: int = 0, seed: int = 0,
          max_tasks: Optional[int] = None) -> Tuple[List[Example], List[Example], dict]:
    bad = set(families) & HELD_OUT
    if bad:
        raise SystemExit(f"refusing held-out families {sorted(bad)} (benchmark test / exam)")
    cases = B.collect(families, max_tasks=max_tasks)
    exs: List[Tuple[Example, str]] = []
    dedup = set()
    for c in cases:
        for ex, kind in rows_for_case(c, seeds):
            key = (ex.user, ex.reply)
            if key in dedup:
                continue
            dedup.add(key)
            exs.append((ex, kind))
    rng = random.Random(seed)
    rng.shuffle(exs)
    if max_per_kind:
        per: Counter = Counter()
        kept = []
        for ex, kind in exs:
            if per[kind] < max_per_kind:
                per[kind] += 1
                kept.append((ex, kind))
        exs = kept
    tasks = sorted({ex.meta["task"] for ex, _ in exs})
    rng.shuffle(tasks)
    val_tasks = set(tasks[: max(1, int(len(tasks) * val_frac))]) if val_frac > 0 else set()
    train = [ex for ex, _ in exs if ex.meta["task"] not in val_tasks]
    valid = [ex for ex, _ in exs if ex.meta["task"] in val_tasks]
    stats = {"families": list(families), "cases": len(cases), "seeds": seeds, "train": len(train),
             "valid": len(valid),
             "by_label": dict(Counter(ex.meta["label"] for ex in train)),
             "by_mutation": dict(Counter(ex.meta["mutation"] for ex in train)),
             "by_family": dict(Counter(ex.meta["family"] for ex in train))}
    return train, valid, stats


def _write(path: Path, rows: List[Example]) -> None:
    with path.open("w") as fh:
        for ex in rows:
            fh.write(json.dumps(ex.to_json()) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--families", nargs="*", default=list(DEFAULT_FAMILIES))
    ap.add_argument("--seeds", type=int, default=4, help="mutation seeds per (case, mutation)")
    ap.add_argument("--val-frac", type=float, default=0.1)
    ap.add_argument("--max-per-kind", type=int, default=0, help="cap rows per mutation kind (0 = no cap)")
    ap.add_argument("--max-tasks", type=int, default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--append-to", type=Path, default=None,
                    help="also mix these rows into DIR/train.jsonl + DIR/valid.jsonl (e.g. the verifier set), "
                         "shuffled, so one run learns both jobs")
    a = ap.parse_args(argv)
    train, valid, stats = build(a.families, seeds=a.seeds, val_frac=a.val_frac, max_per_kind=a.max_per_kind,
                                seed=a.seed, max_tasks=a.max_tasks)
    a.out.mkdir(parents=True, exist_ok=True)
    _write(a.out / "train.jsonl", train)
    _write(a.out / "valid.jsonl", valid)
    (a.out / "stats.json").write_text(json.dumps(stats, indent=2) + "\n")
    if a.append_to:
        stats["appended_to"] = str(a.append_to)
        for name, rows in (("train.jsonl", train), ("valid.jsonl", valid)):
            p = a.append_to / name
            mixed = [ln for ln in p.read_text().splitlines() if ln.strip()] if p.exists() else []
            stats[f"mixed_{name}"] = {"existing": len(mixed), "repair": len(rows)}
            mixed += [json.dumps(ex.to_json()) for ex in rows]
            random.Random(a.seed).shuffle(mixed)
            p.write_text("".join(x + "\n" for x in mixed))
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
