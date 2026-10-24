#!/usr/bin/env python3
"""
repair_bench.py — wrong-repair benchmark for the taught-skill repair layer (scaffold/agent/desktop/repair).

    python scripts/repair_bench.py                      # test families, all arms, table to stdout
    python scripts/repair_bench.py --split dev --out /tmp/repair_dev.json
    python scripts/repair_bench.py --model local --dry-run          # what a real local-model run would do
    python scripts/repair_bench.py --model local --max-calls 300    # llama-server on 127.0.0.1 (free, local)

Pages: every oracle step of every TRAIN-split app-factory task (scaffold/rl/factory, import only) is
replayed through the factory's HTML driver; the page the step acts on and its target control are one
case. Each case is mutated with each of repair.mutate.MUTATIONS (rename, move, wrap, role, delete,
decoy, delete_decoy); the ground truth (the target's index on the new page, or "deleted") is exact.

Exam integrity: the ood families (wizard, files_tree, calendar) are never rendered, and MiniWoB is not
touched; ``collect`` asserts it. Families are split for honesty: thresholds were set by looking at
``--split dev`` (contacts, prefs, records, mail); the headline is ``--split test`` (docs_hub,
assign, shop, checkpoints).

Arms (all run as the product does: the strict role+name grounder first, repair only when it fails):
  strict          no repair (a unique exact role + name match, else halt)
  rule            + tier-1 similarity re-find, no model
  rule+oracle     + a model tier that is always right (upper bound)
  rule+noisy10    + a model tier wrong 10% of the time (a wrong pick or a wrong abstain)
  rule+greedy     + the literature's confident healer: always takes the shortlist's first item
  rule+local      + LocalPicker (only with --model local; loopback llama-server, capped by --max-calls)

Per mutation and arm: accuracy (right control), wrong (a repair onto the wrong control), abstain,
needs_approval (gated: destructive / send-like). On delete / delete_decoy the right answer is to
abstain. wrong-repair rate = wrong / cases (target <= 2%); false-heal share = wrong / repairs made.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scaffold.agent.desktop.repair import mutate as M  # noqa: E402
from scaffold.agent.desktop.repair.core import Repairer  # noqa: E402
from scaffold.agent.desktop.repair.model import (LocalPicker, greedy_picker, noisy_picker,  # noqa: E402
                                                 oracle_picker, prompt)

DEV = ("contacts", "prefs", "records", "mail")
TEST = ("docs_hub", "assign", "shop", "checkpoints")
EXAM_FAMILIES = ("wizard", "files_tree", "calendar")          # the factory's ood split: never rendered
ACTION_OPS = ("click", "fill", "select")


def ood_families() -> set:
    from scaffold.rl.factory.app import FAMILIES
    import scaffold.rl.factory.catalog  # noqa: F401  (registers families)
    return {n for n, f in FAMILIES.items() if f.ood}


def collect(families, *, max_tasks: Optional[int] = None) -> List[dict]:
    """(task, page html, target index) for every action step of the given TRAIN families' oracles."""
    from scaffold.rl.factory.app import App
    from scaffold.rl.factory.catalog import Catalog
    from scaffold.rl.factory.htmldriver import SimBrowser, StepError
    from scaffold.rl.factory.server import Registry, inproc_transport
    bad = set(families) & (ood_families() | set(EXAM_FAMILIES))
    if bad:
        raise SystemExit(f"refusing exam (ood) families {sorted(bad)}")
    cat = Catalog(list(families))
    cases, seen = [], set()
    for k, (tid, e) in enumerate(sorted(cat.entries.items())):
        if max_tasks is not None and k >= max_tasks:
            break
        assert e.task.split == "train" and e.task.family not in EXAM_FAMILIES, tid
        reg = Registry()
        app = App(e.spec, iid="sim")
        reg.add(app)
        br = SimBrowser(inproc_transport(reg))
        br.open(f"http://127.0.0.1:0/t/sim/{e.spec.start_view}")
        for si, st in enumerate(e.steps):
            if st["op"] in ACTION_OPS:
                try:
                    c = br.find(st["role"], st["name"], st.get("context"))
                except StepError:
                    break
                idx = next(i for i, x in enumerate(br.view.controls) if x is c)
                key = hashlib.sha256(br.html.encode()).hexdigest()[:16] + f":{idx}"
                if key not in seen:
                    seen.add(key)
                    cases.append({"task": tid, "family": e.task.family, "split": e.task.split, "step": si,
                                  "html": br.html, "index": idx, "role": c.role, "name": c.name})
            try:
                br.run_step(st)
            except StepError:
                break
    return cases


def strict_pick(fp, view) -> Optional[int]:
    hits = [i for i, c in enumerate(view.controls) if c.role == fp.role and
            " ".join(c.name.split()).lower() == " ".join(fp.name.split()).lower()]
    if fp.twins > 1:                        # the step was recorded with its region (semantic_step's context)
        hits = [i for i in hits if view.controls[i].context.lower() == fp.context.lower()]
    return hits[0] if len(hits) == 1 else None


def make_arms(model: str, max_calls: int, truth_box: dict) -> Dict[str, Optional[Repairer]]:
    truth = lambda fp: truth_box.get("gt")          # noqa: E731 -- the bench's ground truth for mock pickers
    arms: Dict[str, Optional[Repairer]] = {
        "strict": None, "rule": Repairer(),
        "rule+oracle": Repairer(oracle_picker(truth)),
        "rule+noisy10": Repairer(noisy_picker(truth, 0.10, seed=7)),
        "rule+greedy": Repairer(greedy_picker()),
    }
    if model == "local":
        lp = LocalPicker()
        budget = {"left": max_calls}

        def capped(fp, short):
            if budget["left"] <= 0:
                from scaffold.agent.desktop.repair.model import Pick
                return Pick(None, "bench call cap reached; abstain", calls=0)
            budget["left"] -= 1
            return lp(fp, short)
        arms["rule+local"] = Repairer(capped)
    return arms


def run(cases: List[dict], *, model: str = "none", max_calls: int = 300, seed: int = 0) -> dict:
    truth_box: dict = {}
    arms = make_arms(model, max_calls, truth_box)
    stats: Dict[str, Dict[str, Dict[str, int]]] = {a: defaultdict(lambda: defaultdict(int)) for a in arms}
    model_calls = defaultdict(int)
    n_cases = defaultdict(int)
    examples: List[dict] = []
    for ci, case in enumerate(cases):
        from scaffold.rl.factory.htmldriver import parse
        v0 = parse(case["html"])
        fp = M.ctl_fingerprint(v0, case["index"])
        for kind in M.MUTATIONS:
            rng = random.Random(f"{seed}:{case['task']}:{case['step']}:{case['index']}:{kind}")
            m = M.mutate(case["html"], case["index"], kind, rng)
            if m is None:
                stats["strict"][kind]["n_a"] += 1
                continue
            n_cases[kind] += 1
            cands = M.view_candidates(m.view)
            gt = cands[m.gt] if m.gt is not None else None
            truth_box["gt"] = gt
            sp = strict_pick(fp, m.view)
            for arm, rp in arms.items():
                st = stats[arm][kind]
                st["n"] += 1
                if sp is not None:
                    pick, outcome = sp, "strict"
                elif rp is None:
                    pick, outcome = None, "abstain"
                else:
                    c0 = rp.model_calls
                    res = rp.refind(fp, cands)
                    model_calls[arm] += rp.model_calls - c0
                    if res.status == "repaired":
                        pick, outcome = next(i for i, c in enumerate(cands) if c.ref is res.candidate.ref), res.tier
                    else:
                        pick, outcome = None, ("needs_approval" if res.status == "needs_approval" else "abstain")
                if pick is None:
                    st["needs_approval" if outcome == "needs_approval" else "abstain"] += 1
                elif m.gt is not None and pick == m.gt:
                    st["correct"] += 1
                    st[f"by_{outcome}"] += 1
                else:
                    st["wrong"] += 1
                    st[f"wrong_by_{outcome}"] += 1
                    if arm == "rule" and len(examples) < 25:
                        examples.append({"mutation": kind, "task": case["task"], "target": fp.show(),
                                         "picked": cands[pick].show(), "truth": gt.show() if gt else "DELETED",
                                         "by": outcome})
    return {"cases": len(cases), "mutated": dict(n_cases), "arms": {a: {k: dict(v) for k, v in s.items()}
                                                                    for a, s in stats.items()},
            "model_calls": dict(model_calls), "wrong_examples_rule": examples}


def summarize(res: dict) -> dict:
    out = {}
    for arm, per in res["arms"].items():
        tot = defaultdict(int)
        rows = {}
        for kind in M.MUTATIONS:
            s = per.get(kind, {})
            n = s.get("n", 0)
            if not n:
                continue
            deleted = kind.startswith("delete")
            safe = s.get("abstain", 0) + s.get("needs_approval", 0)
            made = s.get("correct", 0) + s.get("wrong", 0)
            rows[kind] = {"n": n, "accuracy": None if deleted else s.get("correct", 0) / n,
                          "wrong": s.get("wrong", 0) / n, "abstain": safe / n,
                          "abstain_correct": safe / n if deleted else None,
                          "false_heal_share": (s.get("wrong", 0) / made) if made else 0.0,
                          "needs_approval": s.get("needs_approval", 0)}
            for k in ("n", "correct", "wrong"):
                tot[k] += s.get(k, 0)
            tot["abstain"] += safe
            if not deleted:
                tot["n_present"] += n
                tot["correct_present"] += s.get("correct", 0)
            else:
                tot["n_deleted"] += n
                tot["abstain_deleted"] += safe
        made = tot["correct"] + tot["wrong"]
        rows["ALL"] = {"n": tot["n"], "accuracy": tot["correct_present"] / max(tot["n_present"], 1),
                       "wrong": tot["wrong"] / max(tot["n"], 1), "abstain": tot["abstain"] / max(tot["n"], 1),
                       "abstain_correct": tot["abstain_deleted"] / max(tot["n_deleted"], 1),
                       "false_heal_share": tot["wrong"] / made if made else 0.0,
                       "model_calls": res["model_calls"].get(arm, 0)}
        out[arm] = rows
    return out


def table(summary: dict) -> str:
    lines = []
    kinds = list(M.MUTATIONS) + ["ALL"]
    for metric, label in (("accuracy", "re-find accuracy (target present)"), ("wrong", "wrong-repair rate"),
                          ("abstain_correct", "abstain correctness (target deleted)")):
        lines.append(f"\n{label}")
        lines.append("| arm | " + " | ".join(kinds) + " |")
        lines.append("|---|" + "---|" * len(kinds))
        for arm, rows in summary.items():
            cells = []
            for k in kinds:
                v = (rows.get(k) or {}).get(metric)
                cells.append("" if v is None else f"{100 * v:.1f}%")
            lines.append(f"| {arm} | " + " | ".join(cells) + " |")
    lines.append("\ncases per mutation: " + ", ".join(f"{k}={summary['strict'].get(k, {}).get('n', 0)}"
                                                       for k in M.MUTATIONS))
    lines.append("model calls: " + ", ".join(f"{a}={r['ALL'].get('model_calls', 0)}" for a, r in summary.items()))
    return "\n".join(lines)


def dry_run(cases: List[dict], max_calls: int) -> dict:
    """How many shortlists a real model run would see, and how big the prompts are (no model call)."""
    from scaffold.rl.factory.htmldriver import parse
    rp = Repairer()
    amb, chars = 0, 0
    for case in cases:
        v0 = parse(case["html"])
        fp = M.ctl_fingerprint(v0, case["index"])
        for kind in M.MUTATIONS:
            m = M.mutate(case["html"], case["index"], kind,
                         random.Random(f"0:{case['task']}:{case['step']}:{case['index']}:{kind}"))
            if m is None or strict_pick(fp, m.view) is not None:
                continue
            res = rp.refind(fp, M.view_candidates(m.view))
            if res.status == "ambiguous":
                amb += 1
                from scaffold.agent.desktop.repair import similo as S
                ranked = S.rank(fp, M.view_candidates(m.view))
                short = [c for _s, c in S.decide(ranked, fp).shortlist]
                chars += len(prompt(fp, short))
    calls = min(amb, max_calls)
    return {"ambiguous_cases": amb, "would_call": calls, "avg_prompt_chars": round(chars / max(amb, 1)),
            "est_prompt_tokens": round(chars / 4), "cost_usd": 0.0,
            "note": "LocalPicker is loopback-only (llama-server); there is no paid path in this script"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--split", choices=("dev", "test", "all"), default="test")
    ap.add_argument("--families", nargs="*", help="override the family list (train families only)")
    ap.add_argument("--max-tasks", type=int, default=None)
    ap.add_argument("--model", choices=("none", "local"), default="none")
    ap.add_argument("--max-calls", type=int, default=300, help="hard cap on local model calls")
    ap.add_argument("--dry-run", action="store_true", help="with --model local: count, do not call")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    fams = tuple(a.families) if a.families else {"dev": DEV, "test": TEST, "all": DEV + TEST}[a.split]
    t0 = time.time()
    cases = collect(fams, max_tasks=a.max_tasks)
    print(f"{len(cases)} cases from {len(set(c['task'] for c in cases))} tasks, families {', '.join(fams)} "
          f"({time.time() - t0:.1f} s)")
    if a.model == "local" and a.dry_run:
        print(json.dumps(dry_run(cases, a.max_calls), indent=2))
        return 0
    res = run(cases, model=a.model, max_calls=a.max_calls, seed=a.seed)
    summ = summarize(res)
    print(table(summ))
    print(f"\n({time.time() - t0:.1f} s)")
    if a.out:
        a.out.write_text(json.dumps({"families": fams, "summary": summ, "raw": res}, indent=1))
        print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
