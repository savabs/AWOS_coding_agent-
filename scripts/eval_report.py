#!/usr/bin/env python3
"""Automatic per-run evaluation report for a job_series results file.

    python scripts/eval_report.py <results.json> [out_dir]

Writes <out_dir>/report.md and <out_dir>/pareto.svg (out_dir defaults to
<results_dir>/report_<run id>). Implements rules 3-6, 9 and 10 of
docs/research/evaluation_first_principles_2026-10.md:

- small-sample intervals (Wilson and Clopper-Pearson), never +-1.96*SE;
- paired tests chosen by data shape: exact McNemar for one attempt per task,
  paired sign-flip permutation test + paired bootstrap over tasks for K > 1;
- cost and turns as co-primary paired endpoints, with the minimum detectable
  difference (Miller 2411.00640: MDE ~= 2.8 * sqrt(var(diff) / n));
- a solve-rate vs $ Pareto chart;
- per-job verdicts and failing hidden tests so the discordant tasks get read;
- a "Decision (stats rule)" section: KEEP / REJECT / INCONCLUSIVE from the paired
  Bayesian P(delta>0) and task-level exact McNemar (docs/specs/stats_decision_rule.md).

Paired exclusion matches scripts/job_series.py: a (repeat, job) invalid in any
arm is dropped from every arm's per-arm numbers. Pairwise comparisons use the
(repeat, job)s valid in both arms of the pair.

If health.json (from scripts/eval_health.py) sits next to the results file or
in out_dir, the report opens with a VALID / INVALID banner.

Pure stdlib: no scipy, no matplotlib, no model calls.
"""
from __future__ import annotations

import itertools
import json
import math
import random
import statistics
import sys
from pathlib import Path

ALPHA = 0.05
SEED = 20261002
BOOT_N = 10_000
PERM_SAMPLES = 10_000
EXACT_PERM_LIMIT = 2 ** 16
FATAL = {"fatal", "error", "critical"}


# ── intervals and exact tests ────────────────────────────────────────────────

def wilson(k: int, n: int, alpha: float = ALPHA) -> tuple[float, float]:
    """Wilson score interval for k successes out of n."""
    if n <= 0:
        return (0.0, 1.0)
    z = _z(1 - alpha / 2)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def _z(q: float) -> float:
    return statistics.NormalDist().inv_cdf(q)


def _log_pmf(i: int, n: int, p: float) -> float:
    if p <= 0.0:
        return 0.0 if i == 0 else -math.inf
    if p >= 1.0:
        return 0.0 if i == n else -math.inf
    return (math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
            + i * math.log(p) + (n - i) * math.log1p(-p))


def binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p)."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return min(1.0, sum(math.exp(_log_pmf(i, n, p)) for i in range(k + 1)))


def _bisect(f, lo: float = 0.0, hi: float = 1.0, iters: int = 200) -> float:
    """Root of a function decreasing in p, f(lo) > 0 > f(hi)."""
    for _ in range(iters):
        mid = (lo + hi) / 2
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def clopper_pearson(k: int, n: int, alpha: float = ALPHA) -> tuple[float, float]:
    """Exact (Clopper-Pearson) interval, by bisection on the binomial CDF."""
    if n <= 0:
        return (0.0, 1.0)
    a = alpha / 2
    lower = 0.0 if k == 0 else _bisect(lambda p: (1 - binom_cdf(k - 1, n, p)) * -1 + a)
    upper = 1.0 if k == n else _bisect(lambda p: binom_cdf(k, n, p) - a)
    return (lower, upper)


def mcnemar_exact(b: int, c: int) -> float:
    """Exact two-sided McNemar p: binomial test of min(b, c) on b + c at 0.5."""
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * binom_cdf(min(b, c), n, 0.5))


def sign_flip_p(diffs: list[float], samples: int = PERM_SAMPLES, seed: int = SEED) -> tuple[float, str]:
    """Two-sided paired sign-flip permutation test of mean(diffs) == 0.

    Exact when the nonzero diffs give <= 2**16 sign patterns, otherwise
    `samples` random patterns with a fixed seed (p includes the observed one).
    """
    nz = [d for d in diffs if d != 0]
    if not nz:
        return 1.0, "exact"
    obs = abs(sum(nz))
    eps = 1e-12
    if 2 ** len(nz) <= EXACT_PERM_LIMIT:
        hits = total = 0
        for signs in itertools.product((1, -1), repeat=len(nz)):
            total += 1
            hits += abs(sum(s * d for s, d in zip(signs, nz))) >= obs - eps
        return hits / total, "exact"
    rng = random.Random(seed)
    hits = 1
    for _ in range(samples):
        hits += abs(sum(d if rng.random() < 0.5 else -d for d in nz)) >= obs - eps
    return hits / (samples + 1), f"{samples} samples"


def bootstrap_ci(diffs: list[float], n_boot: int = BOOT_N, seed: int = SEED,
                 alpha: float = ALPHA) -> tuple[float, float] | None:
    """Percentile bootstrap CI of the mean, resampling paired units."""
    if not diffs:
        return None
    rng = random.Random(seed)
    n = len(diffs)
    means = sorted(sum(rng.choice(diffs) for _ in range(n)) / n for _ in range(n_boot))
    lo = means[int(math.floor(alpha / 2 * n_boot))]
    hi = means[min(n_boot - 1, int(math.ceil((1 - alpha / 2) * n_boot)) - 1)]
    return (lo, hi)


def mde(diffs: list[float]) -> float | None:
    """Miller's minimum detectable difference (80% power, alpha .05)."""
    if len(diffs) < 2:
        return None
    return 2.8 * math.sqrt(statistics.variance(diffs) / len(diffs))


def pass_hat_k(c: int, n: int, k: int) -> float | None:
    """tau-bench pass^k estimator for one task: C(c, k) / C(n, k)."""
    if n < k or k <= 0:
        return None
    return math.comb(c, k) / math.comb(n, k)


# ── loading and paired exclusion ─────────────────────────────────────────────

def _rep(r: dict) -> int:
    return int(r.get("repeat", 1) or 1)


def dropped(results: list[dict], arms: list[str]) -> dict[tuple[int, int], str]:
    """{(repeat, job): why} for (repeat, job)s invalid in any of `arms`."""
    out: dict[tuple[int, int], str] = {}
    for r in results:
        if r["arm"] in arms and r.get("invalid"):
            key = (_rep(r), r["job"])
            why = f"{r['arm']}: {r.get('invalid_reason')}"
            out[key] = f"{out[key]}; {why}" if key in out else why
    return dict(sorted(out.items()))


def _money(r: dict) -> tuple[float | None, bool]:
    """(dollars, from_billing): reconciled billing when known, else ledger cost."""
    if r.get("billed_usd") is not None:
        return float(r["billed_usd"]), True
    if r.get("cost_usd") is not None:
        return float(r["cost_usd"]), False
    return None, False


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def arm_stats(rows: list[dict], k_rep: int) -> dict:
    n = len(rows)
    solved = sum(bool(r["solved"]) for r in rows)
    money = [_money(r) for r in rows]
    dollars = [m for m, _ in money if m is not None]
    fallback = sum(1 for m, billed in money if m is not None and not billed)
    total = sum(dollars)
    by_job: dict[int, list[bool]] = {}
    for r in rows:
        by_job.setdefault(r["job"], []).append(bool(r["solved"]))
    out = {
        "n": n, "solved": solved, "rate": solved / n if n else None,
        "wilson": wilson(solved, n), "cp": clopper_pearson(solved, n),
        "tasks": len(by_job),
        "pass1": _mean([sum(v) / len(v) for v in by_job.values()]),
        "mean_turns": _mean([float(r.get("turns") or 0) for r in rows]),
        "mean_usd": _mean(dollars), "total_usd": total, "usd_fallback": fallback,
        "usd_per_solved": total / solved if solved else None,
        "solved_per_usd": solved / total if total > 0 else None,
        "mean_minutes": _mean([r.get("minutes") for r in rows]),
    }
    if k_rep > 1:
        out["pass_k"] = _mean([pass_hat_k(sum(v), len(v), k_rep) for v in by_job.values()])
    return out


def paired(results: list[dict], a: str, b: str, k_rep: int) -> dict:
    """Compare arm a vs arm b on (repeat, job)s valid in both; per-task units."""
    drop = dropped(results, [a, b])
    rows = {(r["arm"], _rep(r), r["job"]): r for r in results if r["arm"] in (a, b)}
    keys = sorted({(rep, job) for (_, rep, job) in rows if (rep, job) not in drop
                   and (a, rep, job) in rows and (b, rep, job) in rows})
    tasks: dict[int, list[tuple[dict, dict]]] = {}
    for rep, job in keys:
        tasks.setdefault(job, []).append((rows[(a, rep, job)], rows[(b, rep, job)]))

    def per_task(f):
        out = []
        for pairs in tasks.values():
            vals = [f(x) - f(y) for x, y in pairs if f(x) is not None and f(y) is not None]
            if vals:
                out.append(sum(vals) / len(vals))
        return out

    solve = per_task(lambda r: float(bool(r["solved"])))
    usd = per_task(lambda r: _money(r)[0])
    turns = per_task(lambda r: float(r.get("turns") or 0))
    res = {"a": a, "b": b, "units": len(keys), "tasks": len(tasks),
           "dropped": {f"r{k[0]}:{k[1]}" if k_rep > 1 else str(k[1]): v for k, v in drop.items()},
           "solve_a": sum(bool(rows[(a, *k)]["solved"]) for k in keys),
           "solve_b": sum(bool(rows[(b, *k)]["solved"]) for k in keys),
           "diff": _mean(solve), "mde": mde(solve)}
    if k_rep == 1:
        bb = sum(1 for k in keys if rows[(a, *k)]["solved"] and not rows[(b, *k)]["solved"])
        cc = sum(1 for k in keys if rows[(b, *k)]["solved"] and not rows[(a, *k)]["solved"])
        res.update(test="exact McNemar", n_b=bb, n_c=cc, p=mcnemar_exact(bb, cc),
                   discordant=[f"j{k[1]}" for k in keys
                               if bool(rows[(a, *k)]["solved"]) != bool(rows[(b, *k)]["solved"])])
    else:
        p, how = sign_flip_p(solve)
        res.update(test=f"paired sign-flip permutation ({how})", p=p, ci=bootstrap_ci(solve),
                   discordant=[f"j{j}" for j, pairs in tasks.items()
                               if any(bool(x["solved"]) != bool(y["solved"]) for x, y in pairs)])
    res["usd"] = {"diff": _mean(usd), "ci": bootstrap_ci(usd), "n": len(usd),
                  "p": sign_flip_p(usd)[0] if usd else None}
    res["turns"] = {"diff": _mean(turns), "ci": bootstrap_ci(turns), "n": len(turns),
                    "p": sign_flip_p(turns)[0] if turns else None}
    return res


# ── forward decision rule (docs/specs/stats_decision_rule.md) ────────────────

DECIDE_DRAWS = 20_000
DECIDE_SEED = 20261009
P_KEEP = 0.95
P_REJECT = 0.20
JEFFREYS = 0.5  # Beta(0.5, 0.5) prior on each task's per-arm solve probability


def task_table(results: list[dict], base: str, cand: str) -> dict:
    """{task: (solved_base, n_base, solved_cand, n_cand)} over valid rows.

    The unit is the task (`job`). Repeats are pooled per task and arm, not paired
    by repeat index: repeat r of one arm shares nothing with repeat r of the other
    except the task. A task needs at least one valid row in each arm. Duplicate
    (arm, repeat, job) rows keep the valid one (last valid wins).
    """
    rows: dict[tuple[str, int, int], dict] = {}
    for r in results:
        if r["arm"] not in (base, cand):
            continue
        key = (r["arm"], _rep(r), r["job"])
        if key in rows and not rows[key].get("invalid") and r.get("invalid"):
            continue
        rows[key] = r
    agg: dict[int, list[int]] = {}
    for (arm, _, job), r in rows.items():
        if r.get("invalid"):
            continue
        t = agg.setdefault(job, [0, 0, 0, 0])
        i = 0 if arm == base else 2
        t[i] += bool(r["solved"])
        t[i + 1] += 1
    return {j: tuple(v) for j, v in sorted(agg.items()) if v[1] and v[3]}


def p_delta_positive(table: dict, draws: int = DECIDE_DRAWS, seed: int = DECIDE_SEED,
                     prior: float = JEFFREYS) -> dict:
    """Paired posterior of Δ = mean over tasks of (p_cand − p_base).

    Two noise sources (Wang 2512.21326): data noise (which tasks) via Rubin's
    Bayesian bootstrap — Dirichlet(1,…,1) weights over tasks — and prediction
    noise (which runs) via an independent Beta(prior + s, prior + n − s) posterior
    per task and arm. Returns P(Δ>0), the posterior mean and a 95% credible
    interval, plus the plain Bayesian bootstrap on observed task deltas (ties
    counted ½) for reference.
    """
    tasks = list(table.values())
    if not tasks:
        return {"p": 0.5, "mean": 0.0, "cri": (0.0, 0.0), "p_bb": 0.5, "draws": 0, "seed": seed}
    rng = random.Random(seed)
    obs = [sc / nc - sb / nb for sb, nb, sc, nc in tasks]
    beta = rng.betavariate
    deltas: list[float] = []
    pos = bb_pos = 0.0
    for _ in range(draws):
        w = [rng.expovariate(1.0) for _ in tasks]
        tw = sum(w)
        d = sum(wi * (beta(prior + sc, prior + nc - sc) - beta(prior + sb, prior + nb - sb))
                for wi, (sb, nb, sc, nc) in zip(w, tasks)) / tw
        deltas.append(d)
        pos += d > 0
        o = sum(wi * oi for wi, oi in zip(w, obs)) / tw
        bb_pos += 1.0 if o > 1e-12 else (0.5 if abs(o) <= 1e-12 else 0.0)
    deltas.sort()
    lo = deltas[int(0.025 * draws)]
    hi = deltas[min(draws - 1, int(math.ceil(0.975 * draws)) - 1)]
    return {"p": pos / draws, "mean": sum(deltas) / draws, "cri": (lo, hi),
            "p_bb": bb_pos / draws, "draws": draws, "seed": seed}


def mcnemar_tasks(table: dict) -> dict:
    """Exact McNemar at the task level: b = tasks where cand's solve rate beats
    base's, c = the reverse; ties are concordant. With one run per task this is
    the classic McNemar; with K runs it is the exact sign test on task deltas."""
    b = sum(1 for sb, nb, sc, nc in table.values() if sc / nc > sb / nb)
    c = sum(1 for sb, nb, sc, nc in table.values() if sc / nc < sb / nb)
    return {"b": b, "c": c, "p": mcnemar_exact(b, c)}


def _cost_per_run(results: list[dict], arm: str, tasks) -> float | None:
    xs = [_money(r)[0] for r in results if r["arm"] == arm and r["job"] in tasks
          and not r.get("invalid")]
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def decision(results: list[dict], base: str, cand: str, cost_budget: float | None = None,
             draws: int = DECIDE_DRAWS, seed: int = DECIDE_SEED) -> dict:
    """KEEP / REJECT / INCONCLUSIVE for candidate arm `cand` against `base`.

    KEEP iff P(Δ>0) >= 0.95 and task-level exact McNemar p <= 0.05 and (when
    `cost_budget` is given) cand's mean $/run <= base's * (1 + cost_budget).
    REJECT iff P(Δ>0) <= 0.20. Otherwise INCONCLUSIVE: extend n, do not ship.
    """
    table = task_table(results, base, cand)
    post = p_delta_positive(table, draws=draws, seed=seed)
    mc = mcnemar_tasks(table)
    diffs = [sc / nc - sb / nb for sb, nb, sc, nc in table.values()]
    cb, cc = _cost_per_run(results, base, table), _cost_per_run(results, cand, table)
    ratio = cc / cb if cb and cc is not None else None
    within = cost_budget is None or ratio is None or ratio <= 1 + cost_budget
    reasons = []
    if post["p"] >= P_KEEP and mc["p"] <= ALPHA and within:
        label = "KEEP"
    elif post["p"] <= P_REJECT:
        label = "REJECT"
    else:
        label = "INCONCLUSIVE"
        if post["p"] < P_KEEP:
            reasons.append(f"P(Δ>0) = {post['p']:.3f} < {P_KEEP}")
        if mc["p"] > ALPHA:
            reasons.append(f"McNemar p = {mc['p']:.3f} > {ALPHA}")
        if not within:
            reasons.append(f"cost ×{ratio:.2f} over budget +{100 * cost_budget:.0f}%")
    runs = lambda i: sum(t[i] for t in table.values())  # noqa: E731
    return {"label": label, "base": base, "cand": cand, "n_tasks": len(table),
            "runs_base": (runs(0), runs(1)), "runs_cand": (runs(2), runs(3)),
            "delta_obs": _mean(diffs), "p_pos": post["p"], "p_pos_bb": post["p_bb"],
            "delta_post": post["mean"], "cri": post["cri"],
            "mcnemar_b": mc["b"], "mcnemar_c": mc["c"], "mcnemar_p": mc["p"],
            "mde": mde(diffs), "cost_base": cb, "cost_cand": cc, "cost_ratio": ratio,
            "cost_budget": cost_budget, "reasons": reasons, "draws": post["draws"],
            "seed": post["seed"]}


def decision_lines(d: dict) -> list[str]:
    """Markdown lines describing one decision()."""
    sb, nb = d["runs_base"]
    sc, nc = d["runs_cand"]
    cost = ("-" if d["cost_ratio"] is None else
            f"{_usd(d['cost_cand'])} vs {_usd(d['cost_base'])} per run (×{d['cost_ratio']:.2f})")
    out = [f"- **{d['label']}** — candidate `{d['cand']}` vs base `{d['base']}`, "
           f"{d['n_tasks']} tasks (runs {sc}/{nc} vs {sb}/{nb})",
           f"- P(Δ>0) = {d['p_pos']:.3f} (Dirichlet over tasks × Beta per task, "
           f"{d['draws']} draws, seed {d['seed']}); posterior Δ {_pct(d['delta_post'])} "
           f"95% CrI {_ci(d['cri'])}; observed per-task Δ {_pct(d['delta_obs'])}; "
           f"plain Bayesian bootstrap P = {d['p_pos_bb']:.3f}",
           f"- task-level exact McNemar: b = {d['mcnemar_b']} tasks better, "
           f"c = {d['mcnemar_c']} worse → p = {_p(d['mcnemar_p'])}",
           f"- MDE ≈ {_pct(d['mde'])} · cost {cost}"]
    if d["reasons"]:
        out.append("- why not KEEP: " + "; ".join(d["reasons"]))
    return out


# ── health banner ────────────────────────────────────────────────────────────

def find_health(results_path: Path, out_dir: Path) -> Path | None:
    for d in (results_path.parent, out_dir):
        if (d / "health.json").is_file():
            return d / "health.json"
    return None


def health_banner(path: Path | None) -> list[str]:
    if path is None:
        return ["> **Health: UNKNOWN** — no health.json found; run `scripts/eval_health.py` "
                "before believing this run (protocol rule 2).", ""]
    try:
        h = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"> **Health: UNREADABLE** — {path}: {exc}", ""]
    viol = h.get("violations") or []
    fatal = [v for v in viol if str(v.get("severity", "")).lower() in FATAL]
    other = [v for v in viol if v not in fatal]
    ok = bool(h.get("ok")) and not fatal
    lines = [f"> **{'VALID' if ok else 'INVALID'}** — health check `{path.name}`: "
             f"{len(fatal)} fatal, {len(other)} other violation(s)."]
    if not ok:
        lines.append("> Do not draw conclusions from this run until the harness is fixed.")
    for v in fatal:
        where = " ".join(f"{k}={v[k]}" for k in ("arm", "job", "repeat") if v.get(k) is not None)
        lines.append(f"> - **{v.get('check')}** {where}: {v.get('detail')}")
    lines.append("")
    return lines


# ── formatting ───────────────────────────────────────────────────────────────

def _pct(x) -> str:
    return "-" if x is None else f"{100 * x:.1f}%"


def _ci(t) -> str:
    return "-" if t is None else f"[{100 * t[0]:.1f}, {100 * t[1]:.1f}]"


def _usd(x, digits: int = 4) -> str:
    return "-" if x is None else f"${x:.{digits}f}"


def _usd_ci(t) -> str:
    return "-" if t is None else f"[{t[0]:+.4f}, {t[1]:+.4f}]"


def _num(x, digits: int = 2) -> str:
    return "-" if x is None else f"{x:.{digits}f}"


def _p(p) -> str:
    return "-" if p is None else (f"{p:.4f}" if p >= 1e-4 else f"{p:.2e}")


def _row(cells) -> str:
    return "| " + " | ".join(str(c) for c in cells) + " |"


# ── Pareto chart (inline SVG) ────────────────────────────────────────────────

def pareto_svg(points: list[tuple[str, float, float, tuple[float, float]]]) -> str:
    """points: (arm, mean $/job, solve rate, wilson CI). Log x if costs span >10x."""
    W, H, L, R, T, B = 640, 400, 70, 30, 30, 60
    pts = [p for p in points if p[1] is not None and p[1] > 0 and p[2] is not None]
    xs = [p[1] for p in pts] or [1.0]
    logx = max(xs) / min(xs) > 10
    if logx:
        lo, hi = math.log10(min(xs)) - 0.15, math.log10(max(xs)) + 0.15
        fx = lambda v: L + (math.log10(v) - lo) / (hi - lo) * (W - L - R)  # noqa: E731
        ticks = [10 ** e * m for e in range(math.floor(lo), math.ceil(hi) + 1)
                 for m in (1, 2, 5) if lo <= math.log10(10 ** e * m) <= hi]
    else:
        span = (max(xs) - min(xs)) or max(xs) * 0.5 or 1.0
        lo, hi = max(0.0, min(xs) - 0.2 * span), max(xs) + 0.2 * span
        fx = lambda v: L + (v - lo) / (hi - lo) * (W - L - R)  # noqa: E731
        ticks = [lo + i * (hi - lo) / 5 for i in range(6)]
    fy = lambda v: T + (1 - v) * (H - T - B)  # noqa: E731
    frontier = {p[0] for p in pts
                if not any(q is not p and q[1] <= p[1] and q[2] >= p[2]
                           and (q[1] < p[1] or q[2] > p[2]) for q in pts)}
    palette = ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2"]
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
           f'viewBox="0 0 {W} {H}" font-family="sans-serif" font-size="12">',
           f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
           f'<line x1="{L}" y1="{H - B}" x2="{W - R}" y2="{H - B}" stroke="#333"/>',
           f'<line x1="{L}" y1="{T}" x2="{L}" y2="{H - B}" stroke="#333"/>']
    for v in (0, 0.25, 0.5, 0.75, 1.0):
        y = fy(v)
        out.append(f'<line x1="{L}" y1="{y:.1f}" x2="{W - R}" y2="{y:.1f}" stroke="#eee"/>')
        out.append(f'<text x="{L - 8}" y="{y + 4:.1f}" text-anchor="end">{int(v * 100)}%</text>')
    for t in ticks:
        x = fx(t)
        out.append(f'<line x1="{x:.1f}" y1="{H - B}" x2="{x:.1f}" y2="{H - B + 5}" stroke="#333"/>')
        out.append(f'<text x="{x:.1f}" y="{H - B + 18}" text-anchor="middle">${t:.3g}</text>')
    out.append(f'<text x="{(L + W - R) / 2}" y="{H - 15}" text-anchor="middle">'
               f'mean billed $ per job{" (log scale)" if logx else ""}</text>')
    out.append(f'<text x="18" y="{(T + H - B) / 2}" text-anchor="middle" '
               f'transform="rotate(-90 18 {(T + H - B) / 2})">solve rate (Wilson 95% CI)</text>')
    fr = sorted((p for p in pts if p[0] in frontier), key=lambda p: p[1])
    if len(fr) > 1:
        path = " ".join(f"{fx(p[1]):.1f},{fy(p[2]):.1f}" for p in fr)
        out.append(f'<polyline points="{path}" fill="none" stroke="#999" stroke-dasharray="4 3"/>')
    for i, (arm, x_, y_, ci) in enumerate(pts):
        col = palette[i % len(palette)]
        x, y = fx(x_), fy(y_)
        out.append(f'<line x1="{x:.1f}" y1="{fy(ci[0]):.1f}" x2="{x:.1f}" y2="{fy(ci[1]):.1f}" '
                   f'stroke="{col}" stroke-width="1.5" opacity="0.6"/>')
        ring = ' stroke="#000" stroke-width="1.5"' if arm in frontier else ""
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" fill="{col}"{ring}/>')
        out.append(f'<text x="{x + 9:.1f}" y="{y - 8:.1f}" fill="{col}" font-weight="bold">'
                   f'{_esc(arm)} ({100 * y_:.0f}%, ${x_:.4f})</text>')
    out.append(f'<text x="{W - R}" y="{T - 10}" text-anchor="end" fill="#666">'
               f'outlined = Pareto frontier (cheaper and/or more solves)</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def _esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── report ───────────────────────────────────────────────────────────────────

def _verdict(r: dict) -> str:
    if r.get("invalid"):
        return "invalid"
    return "✓" if r.get("solved") else "✗"


def build_report(data: dict, results_path: Path, out_dir: Path) -> tuple[str, str]:
    results = data.get("results") or []
    arms = list(data.get("arms") or sorted({r["arm"] for r in results}))
    reps = sorted({_rep(r) for r in results}) or [1]
    k_rep = len(reps)
    drop = dropped(results, arms)
    run_id = data.get("timestamp") or results_path.stem
    jobs = sorted({r["job"] for r in results})
    lines: list[str] = [f"# Evaluation report — {data.get('series', '?')} {run_id}", ""]
    lines += health_banner(find_health(results_path, out_dir))

    # 1. header
    pin = data.get("model_pin") or {}
    lines += ["## Run", "",
              f"- results: `{results_path}`",
              f"- run id: `{run_id}`" + (f" (revalidated {data['revalidated_at']})"
                                         if data.get("revalidated_at") else ""),
              f"- series: `{data.get('series', '?')}` · arms: {', '.join(f'`{a}`' for a in arms)}",
              f"- model pin: `{pin.get('model', '?')}`"
              + (f" · blocked: {', '.join(pin.get('blocked_ladder_ids') or [])}"
                 if pin.get("blocked_ladder_ids") else "")]
    for key in ("git_sha", "git_commit", "config_hash", "awos_config_hash", "input_hash"):
        if data.get(key):
            lines.append(f"- {key}: `{data[key]}`")
    if data.get("dry_run"):
        lines.append("- **DRY RUN** — no model was called; numbers are not results.")
    if data.get("stopped"):
        lines.append(f"- **stopped early:** {data['stopped']}")
    lines.append(f"- jobs: {len(jobs)} · repeats K = {k_rep} · rows: {len(results)}")
    if drop:
        lines.append(f"- dropped from all arms (paired exclusion, {len(drop)}):")
        for (rep, job), why in drop.items():
            lines.append(f"  - {'r%d:' % rep if k_rep > 1 else ''}j{job} — {why}")
    else:
        lines.append("- dropped (paired exclusion): none")
    lines.append("")

    # 2. per arm
    stats = {a: arm_stats([r for r in results if r["arm"] == a
                           and (_rep(r), r["job"]) not in drop], k_rep) for a in arms}
    lines += ["## Per arm", "",
              f"Rows valid in every arm only. Intervals are 95%. "
              f"{'With K > 1 the runs of one task are correlated, so the run-level intervals are too narrow; use the paired task-level tests below.' if k_rep > 1 else ''}",
              ""]
    head = ["arm", "solved/n", "rate", "Wilson CI", "Clopper-Pearson CI"]
    if k_rep > 1:
        head += ["pass@1", f"pass^{k_rep}"]
    head += ["mean turns", "mean $/job", "$/solved", "solved per $1", "mean min"]
    lines += [_row(head), _row(["---"] * len(head))]
    for a in arms:
        s = stats[a]
        cells = [f"`{a}`", f"{s['solved']}/{s['n']}", _pct(s["rate"]), _ci(s["wilson"]), _ci(s["cp"])]
        if k_rep > 1:
            cells += [_pct(s["pass1"]), _pct(s.get("pass_k"))]
        cells += [_num(s["mean_turns"], 1), _usd(s["mean_usd"]), _usd(s["usd_per_solved"]),
                  _num(s["solved_per_usd"], 1), _num(s["mean_minutes"])]
        lines.append(_row(cells))
    notes = [f"`{a}`: {stats[a]['usd_fallback']} row(s) had no reconciled billing; ledger "
             f"cost_usd used instead" for a in arms if stats[a]["usd_fallback"]]
    lines.append("")
    if drop:
        own = []
        for a in arms:
            s = arm_stats([r for r in results if r["arm"] == a and not r.get("invalid")], k_rep)
            own.append(f"`{a}` {s['solved']}/{s['n']} ({_pct(s['rate'])}, Wilson {_ci(s['wilson'])})")
        lines += ["Before paired exclusion (each arm on its own valid rows, not comparable "
                  "across arms): " + " · ".join(own) + ".", ""]
    lines += ["$ is reconciled provider billing (`billed_usd`)." + (" " + "; ".join(notes) + "."
                                                                   if notes else ""), ""]

    # 3. paired comparisons
    lines += ["## Paired comparisons", "",
              "Each pair uses the jobs valid in both of its arms. Difference = first − second. "
              + ("K = 1: exact McNemar on the discordant pairs (b = only first solved, "
                 "c = only second solved)." if k_rep == 1 else
                 "K > 1: per-task solve-rate difference; paired sign-flip permutation p and "
                 f"bootstrap CI over tasks (seed {SEED}, {BOOT_N} resamples)."),
              "MDE = minimum detectable difference at 80% power (≈ 2.8·sqrt(var(diff)/n), Miller).",
              ""]
    comps = [paired(results, a, b, k_rep) for a, b in itertools.combinations(arms, 2)]
    for c in comps:
        lines.append(f"### `{c['a']}` vs `{c['b']}` — {c['tasks']} tasks, {c['units']} paired runs")
        lines.append("")
        if c["dropped"]:
            lines.append("- excluded: " + "; ".join(f"j{k} ({v})" for k, v in c["dropped"].items()))
        lines.append(f"- solved: {c['a']} {c['solve_a']}/{c['units']} vs {c['b']} "
                     f"{c['solve_b']}/{c['units']} · mean difference {_pct(c['diff'])}"
                     + (f" · bootstrap CI {_ci(c['ci'])}" if c.get("ci") else ""))
        if k_rep == 1:
            lines.append(f"- discordant: b = {c['n_b']}, c = {c['n_c']} → {c['test']} p = {_p(c['p'])}")
        else:
            lines.append(f"- {c['test']}: p = {_p(c['p'])}")
        sig = c["p"] is not None and c["p"] < ALPHA
        lines.append(f"- **{'Significant' if sig else 'Not significant'} at 0.05.** "
                     + (f"MDE ≈ {_pct(c['mde'])}: a true difference smaller than this would "
                        f"usually go undetected with {c['tasks']} tasks."
                        if c["mde"] is not None else "MDE: not computable (fewer than 2 tasks)."))
        if c["discordant"]:
            lines.append(f"- read these transcripts (discordant tasks): {', '.join(c['discordant'])}")
        u, t = c["usd"], c["turns"]
        lines.append(f"- billed $/job difference: {_usd(u['diff'])} · CI {_usd_ci(u['ci'])} · "
                     f"sign-flip p = {_p(u['p'])} ({u['n']} tasks)")
        lines.append(f"- turns difference: {_num(t['diff'], 1)} · CI "
                     f"{'-' if t['ci'] is None else '[%+.1f, %+.1f]' % t['ci']} · "
                     f"sign-flip p = {_p(t['p'])} ({t['n']} tasks)")
        lines.append("")

    # 3b. forward decision rule
    lines += ["## Decision (stats rule)", "",
              "Rule (`docs/specs/stats_decision_rule.md`): KEEP only if P(Δ>0) ≥ 0.95 and "
              "task-level exact McNemar p ≤ 0.05 (within the cost budget); REJECT if "
              "P(Δ>0) ≤ 0.20; otherwise INCONCLUSIVE — extend n, do not ship. Candidate = "
              "first arm of each pair, base = second; Δ = candidate − base per task.", ""]
    for a, b in itertools.combinations(arms, 2):
        lines.append(f"### `{a}` vs `{b}`")
        lines.append("")
        lines += decision_lines(decision(results, base=b, cand=a))
        lines.append("")

    # 4. per job
    lines += ["## Per job", "",
              "Cell: verdict (✓ solved, ✗ not, invalid) · hidden passed/total · turns · billed $."
              + (" Repeats are listed in order." if k_rep > 1 else ""), ""]
    head = ["job", "id"] + [f"`{a}`" for a in arms]
    lines += [_row(head), _row(["---"] * len(head))]
    idx = {(r["arm"], _rep(r), r["job"]): r for r in results}
    for j in jobs:
        jid = next((r.get("id") for r in results if r["job"] == j), "")
        cells = [str(j), f"`{jid}`"]
        for a in arms:
            parts = []
            for rep in reps:
                r = idx.get((a, rep, j))
                if r is None:
                    parts.append("—")
                    continue
                hp = int(r.get("hidden_passed") or 0)
                hf = int(r.get("hidden_failed") or 0)
                m, billed = _money(r)
                parts.append(f"{_verdict(r)} {hp}/{hp + hf} · {r.get('turns', 0)}t · "
                             f"{_usd(m)}{'' if billed or m is None else '*'}")
            cells.append("<br>".join(parts))
        lines.append(_row(cells))
    lines += ["", "\\* ledger cost (no reconciled billing for that row).", ""]

    # 5. failures
    lines += ["## Failures (valid rows)", "", "Includes valid rows of jobs dropped by paired exclusion.", ""]
    counts: dict[str, dict[str, int]] = {}
    for a in arms:
        bad = [r for r in results if r["arm"] == a and not r.get("invalid") and not r.get("solved")]
        lines.append(f"### `{a}` — {len(bad)} unsolved valid run(s)")
        lines.append("")
        if not bad:
            lines += ["none", ""]
            continue
        for r in sorted(bad, key=lambda r: (r["job"], _rep(r))):
            tests = r.get("failing_tests") or []
            for t in tests:
                counts.setdefault(t, {}).setdefault(a, 0)
                counts[t][a] += 1
            tag = f"j{r['job']}" + (f" r{_rep(r)}" if k_rep > 1 else "")
            if tests:
                lines.append(f"- {tag} `{r.get('id', '')}`: " + ", ".join(f"`{t}`" for t in tests))
            else:
                why = "own tests broken" if r.get("visible_tests_ok") is False else "no hidden test failed"
                lines.append(f"- {tag} `{r.get('id', '')}`: ({why}; "
                             f"visible failing: {', '.join(r.get('visible_failing') or []) or '-'})")
        lines.append("")
    if counts:
        lines += ["### Failing hidden tests across arms", ""]
        head = ["test"] + [f"`{a}`" for a in arms] + ["total"]
        lines += [_row(head), _row(["---"] * len(head))]
        for t, by in sorted(counts.items(), key=lambda kv: (-sum(kv[1].values()), kv[0])):
            lines.append(_row([f"`{t}`"] + [by.get(a, 0) for a in arms] + [sum(by.values())]))
        lines.append("")

    # 6. pareto
    pts = [(a, stats[a]["mean_usd"], stats[a]["rate"], stats[a]["wilson"]) for a in arms]
    svg = pareto_svg(pts)
    lines += ["## Pareto: solve rate vs $", "", "![pareto](pareto.svg)", "",
              _row(["arm", "mean $/job", "solve rate"]), _row(["---"] * 3)]
    lines += [_row([f"`{a}`", _usd(x), _pct(y)]) for a, x, y, _ in pts]
    lines += ["", "Protocol rule 9: compare against a simple retry baseline at the same budget "
              "before claiming a cost win.", ""]
    return "\n".join(lines), svg


def write_report(results_path: Path, out_dir: Path) -> Path:
    results_path = Path(results_path)
    out_dir = Path(out_dir)
    data = json.loads(results_path.read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)
    md, svg = build_report(data, results_path, out_dir)
    (out_dir / "pareto.svg").write_text(svg, encoding="utf-8")
    path = out_dir / "report.md"
    path.write_text(md, encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 2
    src = Path(argv[0])
    out = Path(argv[1]) if len(argv) > 1 else src.parent / f"report_{src.stem}"
    path = write_report(src, out)
    print(f"[eval_report] wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
