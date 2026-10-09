#!/usr/bin/env python3
"""capability_map.py — per-repo x mutation-type Beta posterior of the local
model's verified solve rate, from job_series results on practice series (T9).

Spec: docs/specs/idle_practice.md §5.

    python scripts/practice/capability_map.py --results R1.json [R2.json ...]
        --series-root <practice series root> [--arm off] [--threshold 0.5]
        [--prior 1,1] [--out capability_map.json]

Each results file is job_series.py's results JSON ({"series", "results":[{"arm",
"solved","invalid",...}]}). The series' practice.json (written by make_tasks.py)
gives the repo and the mutation type. Invalid rows (infrastructure failures)
are excluded: they are not evidence about the model.

Posterior: theta | s, f ~ Beta(a0 + s, b0 + f), uniform Beta(1,1) by default.
Reported per (repo, op) and pooled per repo (op "*"): mean, 90% equal-tailed
credible interval, and P(theta >= threshold) — the quantity a router reads
("escalate when P(theta_local >= t) is low", trick book §2.5). Pure Python
(no scipy): regularized incomplete beta by Lentz's continued fraction.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

# ── Beta distribution math ────────────────────────────────────────────────────


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta (Numerical Recipes 6.4, Lentz)."""
    tiny, eps = 1e-300, 3e-14
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 1000):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def beta_cdf(x: float, a: float, b: float) -> float:
    """Regularized incomplete beta I_x(a, b) = P(theta <= x), theta ~ Beta(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbt = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
           + a * math.log(x) + b * math.log1p(-x))
    bt = math.exp(lbt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def beta_ppf(q: float, a: float, b: float) -> float:
    """Quantile by bisection (monotone cdf; 60 halvings ~ 1e-18)."""
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if beta_cdf(mid, a, b) < q:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def posterior(solved: int, failed: int, a0: float = 1.0, b0: float = 1.0,
              threshold: float = 0.5, level: float = 0.90) -> dict:
    a, b = a0 + solved, b0 + failed
    tail = (1.0 - level) / 2
    return {
        "n": solved + failed, "solved": solved, "failed": failed,
        "alpha": a, "beta": b,
        "mean": round(a / (a + b), 4),
        "lo": round(beta_ppf(tail, a, b), 4), "hi": round(beta_ppf(1 - tail, a, b), 4),
        "threshold": threshold,
        "p_ge_threshold": round(1.0 - beta_cdf(threshold, a, b), 4),
    }


# ── aggregation ───────────────────────────────────────────────────────────────


def series_meta(series: str, series_root: Path | None) -> dict:
    if series_root is not None:
        p = series_root / series / "practice.json"
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except ValueError:
                pass
    # fallback: prac_<repo>_<NNN>
    parts = series.split("_")
    repo = "_".join(parts[1:-1]) if series.startswith("prac_") and len(parts) > 2 else series
    return {"repo": repo, "op": "unknown"}


def collect(results_docs: list[dict], series_root: Path | None, arm: str | None) -> dict:
    """{(repo, op): [solved, failed]} from job_series results documents."""
    counts: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for doc in results_docs:
        meta = series_meta(doc.get("series", ""), series_root)
        repo, op = meta.get("repo", "?"), meta.get("op", "unknown")
        for r in doc.get("results") or []:
            if arm is not None and r.get("arm") != arm:
                continue
            if r.get("invalid"):
                continue
            counts[(repo, op)][0 if r.get("solved") else 1] += 1
    return dict(counts)


def build_map(counts: dict[tuple[str, str], list[int]], a0: float = 1.0, b0: float = 1.0,
              threshold: float = 0.5) -> dict:
    """{repo: {op: posterior, "*": pooled posterior}}."""
    out: dict[str, dict] = {}
    pooled: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for (repo, op), (s, f) in sorted(counts.items()):
        out.setdefault(repo, {})[op] = posterior(s, f, a0, b0, threshold)
        pooled[repo][0] += s
        pooled[repo][1] += f
    for repo, (s, f) in pooled.items():
        out[repo]["*"] = posterior(s, f, a0, b0, threshold)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", nargs="+", type=Path, required=True)
    ap.add_argument("--series-root", type=Path, default=None)
    ap.add_argument("--arm", default=None, help="only rows of this arm (default: all)")
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--prior", default="1,1", help="a0,b0 of the Beta prior")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    a0, b0 = (float(x) for x in a.prior.split(","))
    docs = [json.loads(p.read_text(encoding="utf-8")) for p in a.results]
    cmap = build_map(collect(docs, a.series_root, a.arm), a0, b0, a.threshold)
    text = json.dumps(cmap, indent=2)
    if a.out:
        a.out.write_text(text + "\n", encoding="utf-8")
    for repo, ops in cmap.items():
        for op, p in sorted(ops.items()):
            print(f"{repo:<20} {op:<20} {p['solved']:>3}/{p['n']:<3} mean {p['mean']:.2f} "
                  f"90% [{p['lo']:.2f}, {p['hi']:.2f}]  P(>={p['threshold']}) {p['p_ge_threshold']:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
