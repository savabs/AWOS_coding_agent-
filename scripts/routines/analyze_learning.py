#!/usr/bin/env python3
"""Analyse Track C (learning over use): docs/specs/ablation_learning_over_use.md.

    python scripts/routines/analyze_learning.py runs/track_c/out/job_series_*.json \
        [--base off --cand learn] [--json out.json] [--md out.md]

Each input is one job_series results file (one learn_fXX series, one or more
repeats). Rows are re-keyed so tasks are unique across series:
job_key = 100 * family_index + position, and repeats from different files of
the same series get distinct repeat numbers. Pure stdlib; no model calls.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import random
from collections import defaultdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("eval_report", _HERE.parent / "eval_report.py")
er = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(er)

FAMILIES = ["f01", "f03", "f04", "f07", "f08", "f09"]
NEAR_MISS_POS = 5
LEARNABLE = (2, 3, 4)
DRAWS, SEED = 20_000, 20261009


def load_rows(paths: list[Path]) -> list[dict]:
    rows: list[dict] = []
    seen: dict[str, int] = defaultdict(int)
    for p in sorted(paths):
        data = json.loads(p.read_text(encoding="utf-8"))
        series = data.get("series", "")
        fam = series.replace("learn_", "")
        if fam not in FAMILIES:
            continue
        seen[series] += 1
        offset = 100 * (seen[series] - 1)
        for r in data.get("results") or []:
            r = dict(r)
            r["series"], r["position"] = series, int(r["job"])
            r["job"] = 100 * (FAMILIES.index(fam) + 1) + int(r["job"])
            r["repeat"] = offset + er._rep(r)
            r["source_file"] = p.name
            rows.append(r)
    return rows


def _valid(rows, arm):
    return [r for r in rows if r["arm"] == arm and not r.get("invalid")]


def curves(rows: list[dict], arms: list[str]) -> dict:
    out = {}
    for arm in arms:
        by_pos = defaultdict(list)
        for r in _valid(rows, arm):
            by_pos[r["position"]].append(r)
        out[arm] = {}
        for pos in sorted(by_pos):
            rs = by_pos[pos]
            cost = [er._money(r)[0] or 0.0 for r in rs]
            solved = sum(bool(r["solved"]) for r in rs)
            out[arm][pos] = {
                "runs": len(rs), "solved": solved, "solve_rate": solved / len(rs),
                "mean_cost": sum(cost) / len(rs),
                "mean_turns": sum(int(r.get("turns") or 0) for r in rs) / len(rs),
                "mean_minutes": sum(float(r.get("minutes") or 0) for r in rs) / len(rs),
                "cost_per_solved": (sum(cost) / solved) if solved else None,
                "notebook_cost": sum(float(r.get("notebook_cost_usd") or 0) for r in rs) / len(rs),
                "mean_notebook_chars": sum(int(r.get("notebook_chars") or 0) for r in rs) / len(rs),
            }
    return out


def per_task_means(rows, arm, metric, positions):
    acc = defaultdict(list)
    for r in _valid(rows, arm):
        if r["position"] not in positions:
            continue
        v = er._money(r)[0] if metric == "cost" else r.get(metric)
        if v is not None:
            acc[r["job"]].append(float(v))
    return {k: sum(v) / len(v) for k, v in acc.items()}


def sign_p(b: int, c: int) -> float:
    return er.mcnemar_exact(b, c)


def efficiency(rows, base, cand, metric, positions) -> dict:
    mb, mc = per_task_means(rows, base, metric, positions), per_task_means(rows, cand, metric, positions)
    tasks = sorted(k for k in mb if k in mc and mb[k] > 0 and mc[k] > 0)
    d = [math.log(mc[k] / mb[k]) for k in tasks]
    if not d:
        return {"metric": metric, "n_tasks": 0, "label": "NO DATA"}
    b, c = sum(x < 0 for x in d), sum(x > 0 for x in d)
    rng = random.Random(SEED)
    means = []
    for _ in range(DRAWS):
        w = [rng.gammavariate(1.0, 1.0) for _ in d]
        s = sum(w)
        means.append(sum(wi * di for wi, di in zip(w, d)) / s)
    means.sort()
    p_neg = sum(m < 0 for m in means) / DRAWS
    p = sign_p(b, c)
    lo, hi = means[int(0.025 * DRAWS)], means[int(0.975 * DRAWS) - 1]
    return {"metric": metric, "positions": list(positions), "n_tasks": len(tasks),
            "cheaper": b, "dearer": c, "sign_p": p, "p_mean_d_lt_0": p_neg,
            "geo_ratio": math.exp(sum(d) / len(d)), "geo_ratio_cri": [math.exp(lo), math.exp(hi)],
            "per_task": {str(k): {"base": mb[k], "cand": mc[k]} for k in tasks}}


def label(eff: dict, solve_label: str) -> str:
    if not eff.get("n_tasks"):
        return "NO DATA"
    if eff["p_mean_d_lt_0"] >= 0.95 and eff["sign_p"] <= 0.05 and solve_label != "REJECT":
        return "LEARNING HELPS"
    if eff["p_mean_d_lt_0"] <= 0.05 and eff["sign_p"] <= 0.05:
        return "LEARNING HURTS"
    return "INCONCLUSIVE"


def harm(rows, base, cand) -> list[dict]:
    tab = defaultdict(lambda: {base: [], cand: []})
    for r in rows:
        if r["arm"] in (base, cand) and not r.get("invalid"):
            tab[r["job"]][r["arm"]].append(r)
    out = []
    for job, a in sorted(tab.items()):
        if a[base] and a[cand] and all(r["solved"] for r in a[base]) and not any(r["solved"] for r in a[cand]):
            out.append({"job": job, "series": a[cand][0]["series"], "position": a[cand][0]["position"],
                        "id": a[cand][0].get("id"), "files": sorted({r["source_file"] for r in a[cand]})})
    return out


def analyse(paths, base="off", cand="learn") -> dict:
    rows = load_rows(paths)
    learn_rows = [r for r in rows if r["position"] in LEARNABLE]
    solve = er.decision(learn_rows, base=base, cand=cand, cost_budget=0.5)
    solve_all = er.decision([r for r in rows if r["position"] != NEAR_MISS_POS],
                            base=base, cand=cand, cost_budget=0.5)
    eff = {m: efficiency(rows, base, cand, m, LEARNABLE) for m in ("cost", "turns")}
    placebo = {m: efficiency(rows, base, cand, m, (1,)) for m in ("cost", "turns")}
    nm = {m: efficiency(rows, base, cand, m, (NEAR_MISS_POS,)) for m in ("cost", "turns")}
    spend = sum(er._money(r)[0] or 0 for r in rows) + sum(
        float(fa.get("cost_usd") or 0) for r in rows for fa in (r.get("failed_attempts") or []))
    return {
        "inputs": sorted(str(p) for p in paths), "n_rows": len(rows),
        "invalid_rows": sum(bool(r.get("invalid")) for r in rows),
        "spend_usd": spend,
        "curves": curves(rows, [base, cand]),
        "solve_rule_pos2_4": solve, "solve_rule_pos1_4": solve_all,
        "efficiency_pos2_4": eff, "labels": {m: label(eff[m], solve["label"]) for m in eff},
        "placebo_pos1": placebo, "near_miss_pos5": nm, "harm": harm(rows, base, cand),
    }


def to_md(a: dict, base="off", cand="learn") -> str:
    L = ["# Track C — learning over use: results", "",
         f"rows {a['n_rows']} (invalid {a['invalid_rows']}), spend ${a['spend_usd']:.4f}", "",
         "## Curves per position", "",
         "| pos | arm | runs | solve | mean $ | mean turns | $ / solved | min | notebook chars |",
         "|---|---|---|---|---|---|---|---|---|"]
    poss = sorted({p for arm in a["curves"].values() for p in arm})
    for p in poss:
        for arm in (base, cand):
            c = a["curves"].get(arm, {}).get(p)
            if not c:
                continue
            cps = "-" if c["cost_per_solved"] is None else f"{c['cost_per_solved']:.4f}"
            L.append(f"| {p} | {arm} | {c['runs']} | {c['solved']}/{c['runs']} | {c['mean_cost']:.4f} | "
                     f"{c['mean_turns']:.1f} | {cps} | {c['mean_minutes']:.2f} | {c['mean_notebook_chars']:.0f} |")
    L += ["", "## Solve rate (stats rule, positions 2–4)", ""] + er.decision_lines(a["solve_rule_pos2_4"])
    L += ["", "## Efficiency (primary, positions 2–4)", ""]
    for m, e in a["efficiency_pos2_4"].items():
        if not e.get("n_tasks"):
            L.append(f"- {m}: no data"); continue
        L.append(f"- **{m}: {a['labels'][m]}** — {e['n_tasks']} tasks, learn cheaper on {e['cheaper']}, "
                 f"dearer on {e['dearer']} (sign p = {e['sign_p']:.3f}); P(mean log-ratio < 0) = "
                 f"{e['p_mean_d_lt_0']:.3f}; geo-mean ratio learn/off = {e['geo_ratio']:.3f} "
                 f"[{e['geo_ratio_cri'][0]:.3f}, {e['geo_ratio_cri'][1]:.3f}]")
    for name, key in (("Placebo (position 1, empty memory in both arms)", "placebo_pos1"),
                      ("Near-miss (position 5)", "near_miss_pos5")):
        L += ["", f"## {name}", ""]
        for m, e in a[key].items():
            if e.get("n_tasks"):
                L.append(f"- {m}: {e['n_tasks']} tasks, cheaper {e['cheaper']} / dearer {e['dearer']}, "
                         f"geo-mean ratio {e['geo_ratio']:.3f} [{e['geo_ratio_cri'][0]:.3f}, "
                         f"{e['geo_ratio_cri'][1]:.3f}], P(<0) = {e['p_mean_d_lt_0']:.3f}")
    L += ["", "## Harm check (off always solved, learn never)", ""]
    L += [f"- {h}" for h in a["harm"]] or ["- none"]
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--base", default="off")
    ap.add_argument("--cand", default="learn")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--md", type=Path)
    a = ap.parse_args()
    res = analyse(a.files, a.base, a.cand)
    md = to_md(res, a.base, a.cand)
    if a.json:
        a.json.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    if a.md:
        a.md.write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
