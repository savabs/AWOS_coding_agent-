#!/usr/bin/env python3
"""
exam_report.py — base vs trained on the same exam tasks, with confidence intervals.

    python scripts/rl/exam_report.py --base runs/base/ --trained runs/r3/ \\
        [--base-filter qwen3.5-4b] [--trained-filter r3] [--out runs/exam_r3_vs_base] [--gate-min 0.02]

Inputs are harness ``trajectories.jsonl`` files (scaffold/rl/harness/runner.py) or directories
searched recursively for them (e.g. ``gh run download <id> --dir artifacts/<id>``; shards merge).
``--*-filter`` keeps only files whose path, or records whose trajectory.model, contains the text
(one artifact folder often holds several models). The latest record per (task, sample) wins, as in
runner.summarize; infra errors (trajectory.error) are dropped and counted.

Per task: score = fraction of its samples that passed (success and no unsafe code); with one
sample per task it is 0/1. Only tasks present (and not infra errors) in BOTH arms are compared.

Slices: all, factory_ood99 (the frozen 99 ``ood`` tasks), factory_ood300_ext (meta.exam), the
union factory_ood300, miniwob_holdout, and any other ``<source>_<split>`` present.
Per slice and arm: success = mean task score with a 95% Wilson interval over tasks.
Paired: difference (trained - base) with a 95% percentile bootstrap over tasks (fixed seed),
exact two-sided McNemar on per-task pass (score > 0.5) discordant pairs, unsafe episodes by code.
Gate (NEXT_WEEK_PLAN rule 2): diff >= --gate-min AND bootstrap lower bound > 0 AND trained unsafe
episodes <= base, on the gate slice (factory_ood300 when present, else all).

Writes <out>.md and <out>.json (default: print markdown to stdout only). Stdlib only, no API calls.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

Z95 = 1.959963984540054
BOOT_DRAWS = 10000
BOOT_SEED = 20261024
EXT_TAG = "ood300_ext"                         # scaffold.rl.factory.catalog.EXT_TAG (no import: stdlib only)


# ---------------------------------------------------------------- stats --

def wilson(k: float, n: int, z: float = Z95) -> Tuple[float, float]:
    """95% Wilson score interval for k successes of n (k may be fractional: mean of per-task rates)."""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, mid - half), min(1.0, mid + half))


def mcnemar_exact(b: int, c: int) -> float:
    """Exact two-sided McNemar p: binomial(b + c, 0.5) tail at min(b, c), doubled, capped at 1."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def bootstrap_diff(pairs: List[Tuple[float, float]], draws: int = BOOT_DRAWS,
                   seed: int = BOOT_SEED) -> Tuple[float, float]:
    """95% percentile bootstrap CI of mean(trained - base), resampling tasks with replacement."""
    n = len(pairs)
    if n == 0:
        return (0.0, 0.0)
    d = [t - b for b, t in pairs]
    rng = random.Random(seed)
    means = sorted(sum(rng.choices(d, k=n)) / n for _ in range(draws))
    return (means[int(0.025 * draws)], means[min(draws - 1, int(math.ceil(0.975 * draws)) - 1)])


# ---------------------------------------------------------------- input --

def find_files(paths: Iterable[str], filt: str = "") -> List[Path]:
    out: List[Path] = []
    for p in map(Path, paths):
        fs = sorted(p.rglob("trajectories.jsonl")) if p.is_dir() else ([p] if p.is_file() else [])
        out += [f for f in fs if not filt or filt in str(f)]
    return out


def load_arm(paths: Iterable[str], filt: str = "") -> dict:
    """{"tasks": {tid: task dict}, "eps": {tid: [record, ...]}, "infra_errors": n, "files": [..]}."""
    files = find_files(paths, filt)
    # a filter that matches no path is applied to trajectory.model instead
    by_path = bool(filt) and bool(files)
    if filt and not files:
        files = find_files(paths)
    latest: Dict[Tuple[str, int], dict] = {}
    bad_lines = 0
    for f in files:
        for ln in f.read_text().splitlines():
            if not ln.strip():
                continue
            try:
                r = json.loads(ln)
            except ValueError:
                bad_lines += 1
                continue
            if filt and not by_path and filt not in str((r.get("trajectory") or {}).get("model", "")):
                continue
            latest[(r["task"]["id"], int(r.get("sample", 0)))] = r
    tasks, eps, errs = {}, defaultdict(list), 0
    for (tid, _), r in sorted(latest.items()):
        if (r.get("trajectory") or {}).get("error"):
            errs += 1
            continue
        tasks[tid] = r["task"]
        eps[tid].append(r)
    return {"tasks": tasks, "eps": dict(eps), "infra_errors": errs, "bad_lines": bad_lines,
            "files": [str(f) for f in files]}


def passed(r: dict) -> bool:
    sc = r.get("score") or {}
    return bool(sc.get("success")) and not sc.get("unsafe")


def task_score(rs: List[dict]) -> float:
    return sum(map(passed, rs)) / len(rs)


def slices_of(task: dict) -> List[str]:
    src, split = task.get("source") or "?", task.get("split") or "?"
    meta = task.get("meta") or {}
    if src == "factory" and split == "ood":
        return ["factory_ood300", "factory_ood300_ext" if meta.get("exam") == EXT_TAG else "factory_ood99"]
    return [f"{src}_{split}"]


# --------------------------------------------------------------- report --

def _arm_block(tids: List[str], arm: dict) -> dict:
    s = [task_score(arm["eps"][t]) for t in tids]
    k, n = sum(s), len(s)
    lo, hi = wilson(k, n)
    rs = [r for t in tids for r in arm["eps"][t]]
    uc = Counter(code for r in rs for code in ((r.get("score") or {}).get("unsafe") or []))
    return {"tasks": n, "episodes": len(rs), "success": round(k / n, 4) if n else None,
            "wilson95": [round(lo, 4), round(hi, 4)],
            "unsafe_episodes": sum(1 for r in rs if (r.get("score") or {}).get("unsafe")),
            "unsafe_by_code": dict(sorted(uc.items()))}


def _paired(tids: List[str], base: dict, trained: dict, boot: bool = True) -> dict:
    pairs = [(task_score(base["eps"][t]), task_score(trained["eps"][t])) for t in tids]
    b = sum(1 for x, y in pairs if x > 0.5 >= y)        # base passes, trained fails
    c = sum(1 for x, y in pairs if y > 0.5 >= x)        # trained passes, base fails
    d = {"diff": round(sum(y - x for x, y in pairs) / len(pairs), 4) if pairs else None,
         "mcnemar": {"base_only": b, "trained_only": c, "p": round(mcnemar_exact(b, c), 6)}}
    if boot:
        lo, hi = bootstrap_diff(pairs)
        d["bootstrap95"] = [round(lo, 4), round(hi, 4)]
    return d


def report(base: dict, trained: dict, gate_min: float = 0.02, labels=("base", "trained")) -> dict:
    common = sorted(set(base["eps"]) & set(trained["eps"]))
    groups: Dict[str, List[str]] = defaultdict(list)
    fams: Dict[str, List[str]] = defaultdict(list)
    for t in common:
        groups["all"].append(t)
        for s in slices_of(base["tasks"][t]):
            groups[s].append(t)
        task = base["tasks"][t]
        fams[f"{task.get('source') or '?'}/{task.get('family') or '?'}"].append(t)
    order = ["all", "factory_ood300", "factory_ood99", "factory_ood300_ext", "miniwob_holdout"]
    names = [g for g in order if g in groups] + sorted(g for g in groups if g not in order)
    split_rows = {g: {labels[0]: _arm_block(groups[g], base), labels[1]: _arm_block(groups[g], trained),
                      **_paired(groups[g], base, trained)} for g in names}
    fam_rows = {f: {labels[0]: _arm_block(ts, base), labels[1]: _arm_block(ts, trained),
                    **_paired(ts, base, trained, boot=False)} for f, ts in sorted(fams.items())}
    gs = "factory_ood300" if "factory_ood300" in split_rows else "all"
    gate = None
    if gs in split_rows:
        g = split_rows[gs]
        ok_d = g["diff"] is not None and g["diff"] >= gate_min
        ok_ci = g["bootstrap95"][0] > 0
        ok_u = g[labels[1]]["unsafe_episodes"] <= g[labels[0]]["unsafe_episodes"]
        gate = {"slice": gs, "min_diff": gate_min, "diff_ok": ok_d, "ci_excludes_0": ok_ci,
                "unsafe_not_up": ok_u, "pass": ok_d and ok_ci and ok_u}
    return {"labels": list(labels), "paired_tasks": len(common),
            "unpaired": {labels[0] + "_only": len(set(base["eps"]) - set(trained["eps"])),
                         labels[1] + "_only": len(set(trained["eps"]) - set(base["eps"]))},
            "infra_errors": {labels[0]: base["infra_errors"], labels[1]: trained["infra_errors"]},
            "samples_per_task": {labels[0]: sorted({len(v) for v in base["eps"].values()}),
                                 labels[1]: sorted({len(v) for v in trained["eps"].values()})},
            "bootstrap": {"draws": BOOT_DRAWS, "seed": BOOT_SEED},
            "by_split": split_rows, "by_family": fam_rows, "gate": gate}


def _pct(x: Optional[float]) -> str:
    return "-" if x is None else f"{100 * x:.1f}"


def _ci(ci) -> str:
    return f"[{100 * ci[0]:.1f}, {100 * ci[1]:.1f}]"


def to_markdown(r: dict) -> str:
    b, t = r["labels"]
    L = [f"# Exam report: {t} vs {b}", "",
         f"paired tasks {r['paired_tasks']}; unpaired {r['unpaired']}; infra errors dropped {r['infra_errors']}; "
         f"samples/task {r['samples_per_task']}", "",
         "Success = % of tasks passed (success, no unsafe). CI = 95% Wilson (per arm), 95% bootstrap over tasks "
         f"(diff, {r['bootstrap']['draws']} draws). McNemar = exact two-sided on discordant tasks.", "",
         "## By split", "",
         f"| slice | tasks | {b} % | {b} CI | {t} % | {t} CI | diff pts | diff CI | {b}-only | {t}-only | McNemar p "
         f"| unsafe {b} | unsafe {t} |",
         "|---|" + "---|" * 12]
    for g, v in r["by_split"].items():
        L.append(f"| {g} | {v[b]['tasks']} | {_pct(v[b]['success'])} | {_ci(v[b]['wilson95'])} | "
                 f"{_pct(v[t]['success'])} | {_ci(v[t]['wilson95'])} | {_pct(v['diff'])} | {_ci(v['bootstrap95'])} | "
                 f"{v['mcnemar']['base_only']} | {v['mcnemar']['trained_only']} | {v['mcnemar']['p']:.4g} | "
                 f"{v[b]['unsafe_episodes']} | {v[t]['unsafe_episodes']} |")
    codes = sorted({c for v in r["by_split"].values() for a in (b, t) for c in v[a]["unsafe_by_code"]})
    if codes:
        allv = r["by_split"]["all"]
        L += ["", "## Unsafe episodes by code (all paired tasks)", "", f"| code | {b} | {t} |", "|---|---|---|"]
        L += [f"| {c} | {allv[b]['unsafe_by_code'].get(c, 0)} | {allv[t]['unsafe_by_code'].get(c, 0)} |" for c in codes]
    L += ["", "## By family", "",
          f"| family | tasks | {b} % | {t} % | diff pts | {b}-only | {t}-only | McNemar p | unsafe {b} | unsafe {t} |",
          "|---|" + "---|" * 9]
    for f, v in r["by_family"].items():
        L.append(f"| {f} | {v[b]['tasks']} | {_pct(v[b]['success'])} | {_pct(v[t]['success'])} | {_pct(v['diff'])} | "
                 f"{v['mcnemar']['base_only']} | {v['mcnemar']['trained_only']} | {v['mcnemar']['p']:.4g} | "
                 f"{v[b]['unsafe_episodes']} | {v[t]['unsafe_episodes']} |")
    g = r.get("gate")
    if g:
        L += ["", f"## Gate ({g['slice']}): {'PASS' if g['pass'] else 'FAIL'}", "",
              f"- diff >= {100 * g['min_diff']:.0f} pts: {g['diff_ok']}",
              f"- bootstrap CI excludes 0: {g['ci_excludes_0']}",
              f"- unsafe episodes not up: {g['unsafe_not_up']}"]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="exam_report.py", description=__doc__.split("\n\n")[1].strip())
    ap.add_argument("--base", nargs="+", required=True, help="trajectories.jsonl files or directories")
    ap.add_argument("--trained", nargs="+", required=True)
    ap.add_argument("--base-filter", default="")
    ap.add_argument("--trained-filter", default="")
    ap.add_argument("--base-label", default="base")
    ap.add_argument("--trained-label", default="trained")
    ap.add_argument("--gate-min", type=float, default=0.02)
    ap.add_argument("--out", help="write <out>.md and <out>.json")
    a = ap.parse_args(argv)
    if a.base_label == a.trained_label:
        ap.error("--base-label and --trained-label must differ")
    base, trained = load_arm(a.base, a.base_filter), load_arm(a.trained, a.trained_filter)
    if not base["eps"] or not trained["eps"]:
        print(f"no records: base {len(base['files'])} files, trained {len(trained['files'])} files", file=sys.stderr)
        return 2
    r = report(base, trained, a.gate_min, (a.base_label, a.trained_label))
    md = to_markdown(r)
    if a.out:
        Path(a.out + ".md").parent.mkdir(parents=True, exist_ok=True)
        Path(a.out + ".md").write_text(md)
        Path(a.out + ".json").write_text(json.dumps(r, indent=2) + "\n")
    print(md)
    return 0 if r["paired_tasks"] else 2


if __name__ == "__main__":
    sys.exit(main())
