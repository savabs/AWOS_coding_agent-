#!/usr/bin/env python3
"""Apply the forward stats decision rule to a combined job_series results file.

    python scripts/stats_audit.py <combined.json> --a BASE --b CANDIDATE [--cost-budget 0.5]
    python scripts/stats_audit.py --power [--tasks 28 66 100] [--repeats 2 3]

Verdict: KEEP only if P(Δ>0) >= 0.95 AND task-level exact McNemar p <= 0.05 (and,
with --cost-budget, candidate $/run <= base $/run * (1 + budget)); REJECT if
P(Δ>0) <= 0.20; otherwise INCONCLUSIVE (extend n, do not ship). Δ = b − a.
Rule and units: docs/specs/stats_decision_rule.md.

--power prints the minimum detectable effect (80% power) of the McNemar leg of the
rule for n tasks x k repeats, by simulation (see the spec for the task model).

Pure stdlib, deterministic (fixed seeds), no model calls.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import random
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("eval_report", _HERE / "eval_report.py")
er = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(er)

# Per-task control solve rates of the five audited ablation control arms (55 tasks,
# K = 2, so rates are 0, .5 or 1: 32 x 1.0, 13 x 0.5, 10 x 0.0). Shape of the AWOS task pool for power planning:
# many tasks always solved, a few never, about a quarter flaky.
BASE_RATE_POOL = (1.0,) * 32 + (0.5,) * 13 + (0.0,) * 10


def audit(path: Path, a: str, b: str, cost_budget: float | None = None,
          draws: int = er.DECIDE_DRAWS) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    results = data.get("results") or []
    arms = {r["arm"] for r in results}
    for arm in (a, b):
        if arm not in arms:
            raise SystemExit(f"arm {arm!r} not in {sorted(arms)}")
    return er.decision(results, base=a, cand=b, cost_budget=cost_budget, draws=draws)


def _true_rate(rng: random.Random, observed: float, k_obs: int = 2) -> float:
    """A true task rate consistent with an observed K=2 rate (Jeffreys posterior)."""
    s = round(observed * k_obs)
    return rng.betavariate(0.5 + s, 0.5 + k_obs - s)


def power_mcnemar(n: int, k: int, delta: float, sims: int = 2000, seed: int = 7) -> float:
    """Share of simulated n-task x k-repeat experiments where the task-level exact
    McNemar p <= 0.05 in the candidate's favour, for a true mean uplift `delta`.

    Task model: base rate p_t drawn from BASE_RATE_POOL (smoothed by a Jeffreys
    posterior); the candidate gains in proportion to headroom,
    p'_t = min(1, p_t + delta * (1 - p_t) / mean(1 - p)).
    """
    rng = random.Random(seed)
    head = 1 - sum(BASE_RATE_POOL) / len(BASE_RATE_POOL)
    hits = 0
    for _ in range(sims):
        b = c = 0
        for _ in range(n):
            p = _true_rate(rng, rng.choice(BASE_RATE_POOL))
            q = min(1.0, p + delta * (1 - p) / head)
            sa = sum(rng.random() < p for _ in range(k))
            sb = sum(rng.random() < q for _ in range(k))
            b += sb > sa
            c += sb < sa
        hits += b > c and er.mcnemar_exact(b, c) <= er.ALPHA
    return hits / sims


def power_rule(n: int, k: int, delta: float, sims: int = 100, draws: int = 1000,
               seed: int = 11) -> float:
    """Share of simulated experiments the full rule labels KEEP (both legs), same
    task model as power_mcnemar. Slow; used to check the McNemar-leg MDE."""
    rng = random.Random(seed)
    head = 1 - sum(BASE_RATE_POOL) / len(BASE_RATE_POOL)
    keep = 0
    for i in range(sims):
        table = {}
        for t in range(n):
            p = _true_rate(rng, rng.choice(BASE_RATE_POOL))
            q = min(1.0, p + delta * (1 - p) / head)
            table[t] = (sum(rng.random() < p for _ in range(k)), k,
                        sum(rng.random() < q for _ in range(k)), k)
        mc = er.mcnemar_tasks(table)
        if mc["b"] > mc["c"] and mc["p"] <= er.ALPHA:
            keep += er.p_delta_positive(table, draws=draws, seed=seed + i)["p"] >= er.P_KEEP
    return keep / sims


def mde_table(tasks=(28, 66, 100), repeats=(2, 3), power: float = 0.8,
              sims: int = 2000) -> list[tuple[int, int, float | None]]:
    """[(n, k, smallest uplift in pp on a 1-pp grid reaching `power`)]."""
    out = []
    for n in tasks:
        for k in repeats:
            lo, hi = 0, 60
            if power_mcnemar(n, k, hi / 100, sims) < power:
                out.append((n, k, None))
                continue
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if power_mcnemar(n, k, mid / 100, sims) >= power:
                    hi = mid
                else:
                    lo = mid
            out.append((n, k, hi / 100))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("combined", nargs="?", type=Path)
    ap.add_argument("--a", help="base / control arm")
    ap.add_argument("--b", help="candidate arm")
    ap.add_argument("--cost-budget", type=float, default=None,
                    help="max relative $/run increase for KEEP, e.g. 0.5 = +50%%")
    ap.add_argument("--draws", type=int, default=er.DECIDE_DRAWS)
    ap.add_argument("--json", action="store_true", help="print the decision as JSON")
    ap.add_argument("--power", action="store_true", help="print the MDE table instead")
    ap.add_argument("--tasks", type=int, nargs="+", default=[28, 66, 100])
    ap.add_argument("--repeats", type=int, nargs="+", default=[2, 3])
    ap.add_argument("--sims", type=int, default=2000)
    args = ap.parse_args(argv)
    if args.power:
        print("| tasks | repeats | MDE (80% power, McNemar leg) | full-rule power at that MDE |")
        print("|---|---|---|---|")
        for n, k, m in mde_table(args.tasks, args.repeats, sims=args.sims):
            full = "-" if m is None else f"{100 * power_rule(n, k, m):.0f}%"
            print(f"| {n} | {k} | {'> 60 pp' if m is None else f'{100 * m:.0f} pp'} | {full} |")
        return 0
    if not (args.combined and args.a and args.b):
        ap.error("need <combined.json> --a BASE --b CANDIDATE (or --power)")
    d = audit(args.combined, args.a, args.b, args.cost_budget, args.draws)
    if args.json:
        print(json.dumps(d, indent=2, default=list))
    else:
        print(f"Decision (stats rule): {d['label']}")
        print("\n".join(er.decision_lines(d)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
