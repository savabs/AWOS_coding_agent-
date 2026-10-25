#!/usr/bin/env python3
"""
judge.py — judge Online-Mind2Web runs with WebJudge (scaffold/rl/web_judge, docs/specs/web_judge.md).

    python scripts/web/judge.py --runs runs/web/A [LABEL=DIR ...] --backend dry --out results.json
    python scripts/web/judge.py --runs A B --backend o4mini --max-usd 12 --out results.json
    python scripts/web/judge.py --runs A --backend webjudge7b --base-url http://127.0.0.1:8000/v1 --out r.json

--backend dry (or --dry-run with any backend) makes no call: it prints the number of calls and the USD
estimate for o4-mini from token counts. --backend o4mini reads OPENAI_API_KEY (never printed) and stops
before any call that could take spending past --max-usd (default 12): the episodes left are not_judged and
the partial results are still written (exit 3). Verdicts are appended to <out>.verdicts.jsonl as they come,
so a second invocation with the same settings resumes; --fresh ignores it.

Output: per-run per-task verdicts, a summary (official and executable success rates, overall and by level,
95% Wilson CIs, blocked / infra_error counted separately) and, for two or more runs, paired comparisons
(exact McNemar) of every run against the first (or the pairs given with --compare A:B).
Exit: 0 complete, 3 stopped by the cap, 2 fatal backend error (e.g. the model is no longer served), 1 usage.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scaffold.rl.web_judge import UPSTREAM_COMMIT, UPSTREAM_REPO  # noqa: E402
from scaffold.rl.web_judge import cost as C  # noqa: E402
from scaffold.rl.web_judge import stats as S  # noqa: E402
from scaffold.rl.web_judge.backends import WEBJUDGE_7B_REVISION, BackendError, OpenAICompatBackend  # noqa: E402
from scaffold.rl.web_judge.judge import SCORE_THRESHOLD, judge_run  # noqa: E402
from scaffold.rl.web_judge.layout import LayoutError, find_episodes, image_size, load_levels  # noqa: E402

OFFICIAL_TASKS = 300


def parse_runs(specs: List[str]) -> List[tuple]:
    out, seen = [], set()
    for s in specs:
        label, sep, path = s.partition("=")
        if not sep:
            label, path = Path(s).name or s, s
        if label in seen:
            raise SystemExit(f"duplicate run label {label!r}: name them LABEL=DIR")
        seen.add(label)
        out.append((label, Path(path)))
    return out


def estimate(runs: Dict[str, list], a) -> Dict:
    """Dry-run numbers from the actual screenshots (sizes read from file headers)."""
    rows, total = {}, {"calls": 0, "prompt_tokens": 0, "expected_output_tokens": 0, "worst_output_tokens": 0,
                       "worst_prompt_tokens": 0, "episodes": 0, "screenshots": 0}
    for label, eps in runs.items():
        r = dict.fromkeys(total, 0)
        for ep in eps:
            if not ep.judgeable and not a.judge_blocked:
                continue
            sizes = [image_size(p) or (1280, 720) for p in ep.screenshots]
            kw = dict(patch_budget=a.image_patch_budget, max_output_tokens=a.max_output_tokens)
            e = C.estimate_episode(ep.task, ep.action_history, sizes, **kw)
            w = C.estimate_episode(ep.task, ep.action_history, sizes, worst_key_images=True, **kw)
            r["calls"] += e.calls
            r["prompt_tokens"] += e.prompt_tokens
            r["worst_prompt_tokens"] += w.prompt_tokens
            r["expected_output_tokens"] += e.expected_output_tokens
            r["worst_output_tokens"] += e.worst_output_tokens
            r["episodes"] += 1
            r["screenshots"] += e.images
        rows[label] = r
        for k in total:
            total[k] += r[k]
    usd = {}
    for tier in ("standard", "flex"):
        p = C.prices_for("o4-mini", tier)
        usd[tier] = {"expected": C.usd(p, total["prompt_tokens"], total["expected_output_tokens"]),
                     "worst": C.usd(p, total["worst_prompt_tokens"], total["worst_output_tokens"])}
        n = total["episodes"]
        usd[tier]["expected_per_300_tasks"] = usd[tier]["expected"] / n * OFFICIAL_TASKS if n else None
    return {"runs": rows, "total": total, "usd": usd, "prices_source": C.PRICING_URL,
            "prices_checked": C.PRICES_CHECKED, "image_patch_budget": a.image_patch_budget,
            "assumptions": {"expected_output_tokens_per_call": C.EXPECTED_OUTPUT,
                            "key_screenshot_fraction": C.EXPECTED_KEY_FRACTION, "chars_per_token": 4}}


def print_estimate(est: Dict, a) -> None:
    t = est["total"]
    print(f"dry run: {t['episodes']} episodes, {t['screenshots']} screenshots, {t['calls']} judge calls "
          f"(1 key-point + 1 per screenshot + 1 outcome per episode)")
    print(f"  prompt tokens ~{t['prompt_tokens']:,} (worst, all screenshots key: ~{t['worst_prompt_tokens']:,}); "
          f"output ~{t['expected_output_tokens']:,} expected, <= {t['worst_output_tokens']:,} at "
          f"max_completion_tokens={a.max_output_tokens}")
    for tier, u in est["usd"].items():
        per = u["expected_per_300_tasks"]
        print(f"  o4-mini {tier:8s}: expected ${u['expected']:.2f}, worst ${u['worst']:.2f}"
              + (f", ~${per:.2f} per {OFFICIAL_TASKS}-task run" if per is not None else ""))
    print(f"  prices: {est['prices_source']} (checked {est['prices_checked']}); cap --max-usd {a.max_usd}")


def load_cache(path: Path, key: Dict) -> Dict[str, Dict[str, Dict]]:
    done: Dict[str, Dict[str, Dict]] = {}
    if not path.exists():
        return done
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("judge_key") == key:
            done.setdefault(row["run"], {})[row["verdict"]["task_id"]] = row["verdict"]
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scripts/web/judge.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs", nargs="+", required=True, help="run dirs (judge layout), optionally LABEL=DIR")
    ap.add_argument("--backend", choices=["o4mini", "webjudge7b", "dry"], default="dry")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--dry-run", action="store_true", help="estimate only, no call (same as --backend dry)")
    ap.add_argument("--max-usd", type=float, default=12.0, help="hard cap for o4mini (default 12)")
    ap.add_argument("--model", default=None, help="default o4-mini / osunlp/WebJudge-7B")
    ap.add_argument("--base-url", default=None, help="webjudge7b server (default $WEBJUDGE_BASE_URL or :8000/v1)")
    ap.add_argument("--service-tier", choices=["standard", "flex"], default="standard",
                    help="o4mini: flex is half price, slower, may 429 (same model)")
    ap.add_argument("--max-output-tokens", type=int, default=None,
                    help="o4mini max_completion_tokens (default 8192); webjudge7b max_tokens (default 512)")
    ap.add_argument("--image-patch-budget", type=int, default=C.DEFAULT_PATCH_BUDGET)
    ap.add_argument("--score-threshold", type=int, default=SCORE_THRESHOLD)
    ap.add_argument("--levels", type=Path, default=None, help="Online-Mind2Web task file or {task_id: level}")
    ap.add_argument("--judge-blocked", action="store_true", help="also send blocked / infra_error episodes")
    ap.add_argument("--workers", type=int, default=4, help="episodes in parallel")
    ap.add_argument("--image-workers", type=int, default=8, help="screenshot calls in parallel per episode")
    ap.add_argument("--compare", nargs="*", default=None, help="pairs A:B (default: every run vs the first)")
    ap.add_argument("--fresh", action="store_true", help="ignore <out>.verdicts.jsonl")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    dry = a.dry_run or a.backend == "dry"
    if a.max_output_tokens is None:
        a.max_output_tokens = 512 if a.backend == "webjudge7b" else 8192
    log = (lambda *x: None) if a.quiet else (lambda *x: print(*x, flush=True))

    try:
        levels = load_levels(a.levels)
        runs = {label: find_episodes(path, levels) for label, path in parse_runs(a.runs)}
    except LayoutError as e:
        print(f"layout error: {e}", file=sys.stderr)
        return 1
    for label, eps in runs.items():
        log(f"run {label}: {len(eps)} episodes ({sum(not e.judgeable for e in eps)} marked blocked/infra_error)")

    est = estimate(runs, a)
    print_estimate(est, a)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    meta = {"upstream": UPSTREAM_REPO, "upstream_commit": UPSTREAM_COMMIT, "mode": "WebJudge_Online_Mind2Web_eval",
            "score_threshold": a.score_threshold, "backend": "dry" if dry else a.backend,
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if dry:
        a.out.write_text(json.dumps({"judge": meta, "estimate": est}, indent=1) + "\n")
        print(f"wrote {a.out} (estimate only)")
        return 0

    budget = C.Budget(a.max_usd if a.backend == "o4mini" else None)
    try:
        backend = OpenAICompatBackend(a.backend, base_url=a.base_url, model=a.model,
                                      max_output_tokens=a.max_output_tokens, service_tier=a.service_tier,
                                      budget=budget if a.backend == "o4mini" else None,
                                      patch_budget=a.image_patch_budget)
    except (BackendError, KeyError, ValueError) as e:
        print(f"backend: {e}", file=sys.stderr)
        return 2
    meta.update(model=backend.model, backend_name=backend.name, service_tier=a.service_tier,
                max_output_tokens=a.max_output_tokens,
                model_revision=WEBJUDGE_7B_REVISION if a.backend == "webjudge7b" else None)
    if a.backend == "o4mini":
        exp = est["usd"][a.service_tier]["expected"]
        if exp > a.max_usd:
            print(f"warning: expected ${exp:.2f} > cap ${a.max_usd:.2f}: the run will stop partway "
                  f"(partial results)")

    key = {"backend": backend.name, "threshold": a.score_threshold, "max_output_tokens": a.max_output_tokens}
    cache_path = a.out.with_name(a.out.name + ".verdicts.jsonl")
    if a.fresh and cache_path.exists():
        cache_path.unlink()
    cache = load_cache(cache_path, key)
    cache_lock = threading.Lock()
    stop = threading.Event()
    results, info_all = {}, {"stopped": None, "fatal": None}
    for label, eps in runs.items():
        def save(v, label=label):
            with cache_lock, open(cache_path, "a") as fh:
                fh.write(json.dumps({"run": label, "judge_key": key, "verdict": v}) + "\n")
        log(f"judging {label} with {backend.name}")
        verdicts, info = judge_run(eps, backend, score_threshold=a.score_threshold, workers=a.workers,
                                   image_workers=a.image_workers, judge_blocked=a.judge_blocked,
                                   done=cache.get(label), on_verdict=save, stop=stop, log=log)
        info_all["stopped"] = info_all["stopped"] or info["stopped"]
        info_all["fatal"] = info_all["fatal"] or info["fatal"]
        results[label] = {"summary": S.summarize(verdicts), "tasks": verdicts}

    labels = list(results)
    pairs = [tuple(p.split(":", 1)) for p in a.compare] if a.compare else [(labels[0], b) for b in labels[1:]]
    comparisons = []
    for x, y in pairs:
        if x not in results or y not in results:
            print(f"--compare {x}:{y}: unknown run label", file=sys.stderr)
            continue
        comparisons.append(S.compare(results[x]["tasks"], results[y]["tasks"], x, y))

    doc = {"judge": meta, "cost": budget.as_dict(), "stopped": info_all["stopped"], "estimate": est,
           "runs": results, "comparisons": comparisons}
    a.out.write_text(json.dumps(doc, indent=1) + "\n")
    print(report(doc))
    print(f"wrote {a.out}")
    if info_all["fatal"]:
        return 2
    return 3 if info_all["stopped"] else 0


def report(doc: Dict) -> str:
    lines = [f"WebJudge ({doc['judge'].get('backend_name')}) — spent ${doc['cost']['spent_usd']:.4f} "
             f"in {doc['cost']['calls']} calls" + (f" — STOPPED: {doc['stopped']}" if doc["stopped"] else "")]
    for label, r in doc["runs"].items():
        s = r["summary"]
        lines.append(f"{label}: {s['n_tasks']} tasks, judged {s['n_judged']}, blocked {s['n_blocked']}, "
                     f"infra_error {s['n_infra_error']}, judge_error {s['n_judge_error']}, "
                     f"not_judged {s['n_not_judged']}{'' if s['complete'] else ' (INCOMPLETE)'}")
        lines.append(f"  official   {S.fmt_rate(s['official'])}")
        lines.append(f"  executable {S.fmt_rate(s['executable'])}")
        for lv, x in s["by_level"].items():
            lines.append(f"  {lv:9s}  {S.fmt_rate(x['official'])}")
    for c in doc["comparisons"]:
        lines.append(f"{c['b']} vs {c['a']}: n={c['n_paired']} paired, {c['a']} only {c['a_only_success']}, "
                     f"{c['b']} only {c['b_only_success']}, McNemar exact p={c['mcnemar_exact_p']:.4g}")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
