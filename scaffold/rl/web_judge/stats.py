"""
stats.py — success-rate summary with 95% Wilson intervals, and the exact McNemar test for paired A/B runs.

Rates (docs/specs/web_judge.md):
    official     successes / scored, scored = judged + blocked + infra_error (harness failures count as
                 failures, as upstream divides by all task dirs). Comparable with the leaderboard when the
                 run is complete (no judge_error / not_judged) and covers the 300 tasks.
    executable   successes / judged episodes the harness ran normally (harness_status ok): the agent's rate
                 when the site let it work. Not comparable with published numbers; for diagnosis.
"""
from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional

from .layout import LEVELS

Z95 = 1.959963984540054


def wilson(k: int, n: int, z: float = Z95) -> Optional[Dict]:
    if n <= 0:
        return None
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return {"k": k, "n": n, "rate": p, "lo": max(0.0, centre - half), "hi": min(1.0, centre + half)}


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value: binomial test of min(b, c) on b + c discordant pairs, p = 1/2."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


SCORED = ("judged", "blocked", "infra_error")


def _rates(vs: List[Dict]) -> Dict:
    scored = [v for v in vs if v.get("status") in SCORED and v.get("predicted_label") is not None]
    ok = [v for v in scored if v.get("status") == "judged" and v.get("harness_status") == "ok"]
    k = sum(int(v["predicted_label"]) for v in scored)
    return {"official": wilson(k, len(scored)),
            "executable": wilson(sum(int(v["predicted_label"]) for v in ok), len(ok))}


def summarize(verdicts: Iterable[Dict]) -> Dict:
    vs = list(verdicts)
    count = lambda s: sum(1 for v in vs if v.get("status") == s)   # noqa: E731
    out = {"n_tasks": len(vs), "n_judged": count("judged"), "n_blocked": count("blocked"),
           "n_infra_error": count("infra_error"), "n_judge_error": count("judge_error"),
           "n_not_judged": count("not_judged"),
           "n_harness_blocked": sum(1 for v in vs if v.get("harness_status") == "blocked"),
           "n_harness_infra_error": sum(1 for v in vs if v.get("harness_status") == "infra_error")}
    out["complete"] = out["n_judge_error"] == 0 and out["n_not_judged"] == 0
    out["successes"] = sum(int(v["predicted_label"]) for v in vs
                           if v.get("status") in SCORED and v.get("predicted_label") is not None)
    out.update(_rates(vs))
    levels = {}
    for lv in list(LEVELS) + ["unknown"]:
        sub = [v for v in vs if (v.get("level") or "unknown") == lv]
        if sub:
            levels[lv] = {"n_tasks": len(sub), **_rates(sub)}
    out["by_level"] = levels
    return out


def compare(a: Iterable[Dict], b: Iterable[Dict], label_a: str = "A", label_b: str = "B") -> Dict:
    """Paired comparison over task_ids scored in both runs (official labels: blocked / infra = 0)."""
    def labels(vs):
        return {v["task_id"]: int(v["predicted_label"]) for v in vs
                if v.get("status") in SCORED and v.get("predicted_label") is not None}
    la, lb = labels(a), labels(b)
    common = sorted(set(la) & set(lb))
    both = sum(1 for t in common if la[t] and lb[t])
    only_a = sum(1 for t in common if la[t] and not lb[t])
    only_b = sum(1 for t in common if lb[t] and not la[t])
    neither = len(common) - both - only_a - only_b
    n = len(common)
    return {"a": label_a, "b": label_b, "n_paired": n, "only_in_a": len(set(la) - set(lb)),
            "only_in_b": len(set(lb) - set(la)), "both_success": both, "a_only_success": only_a,
            "b_only_success": only_b, "both_fail": neither,
            "rate_a": wilson(both + only_a, n), "rate_b": wilson(both + only_b, n),
            "diff_b_minus_a": ((only_b - only_a) / n) if n else None,
            "mcnemar_exact_p": mcnemar_exact(only_a, only_b)}


def fmt_rate(r: Optional[Dict]) -> str:
    if not r:
        return "n/a"
    return f"{100 * r['rate']:.1f}% ({r['k']}/{r['n']}, 95% CI {100 * r['lo']:.1f}-{100 * r['hi']:.1f})"
