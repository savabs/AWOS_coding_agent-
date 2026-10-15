#!/usr/bin/env python3
"""Stage and report Harbor head-to-head runs (ci/HARBOR.md, .github/workflows/harbor.yml).

  stage   (on the runner) one small summary.json per trial — reward, outcome
          class, timings, per-request token usage and the cost recomputed
          uniformly (harbor_agents/pricing.py) — plus Harbor's result.json, the
          verifier reward/output and the agent logs cut to head+tail. Never a
          trajectory.json or anything from the task container's filesystem.
  report  (on the laptop) merge the downloaded artifacts and print, per agent:
          pass rate (Wilson 95% CI; infra errors count as failures, plus the
          rate with them excluded), mean cost per trial at uniform prices, mean
          agent time, infra errors, refusals, agent timeouts; then AWOS paired
          against each other agent on the tasks both ran: exact McNemar when
          every task has one trial per agent (k=1), a paired bootstrap over
          tasks (per-task mean pass) when k>1.

    gh run download <run-id> -D ~/.awos-harness/harbor_dl
    python scripts/harbor_collect.py report ~/.awos-harness/harbor_dl [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from harbor_agents import pricing  # noqa: E402

SUMMARY = "summary.json"
LOG_KEEP_BYTES = 64 * 1024
AWOS = "awos"

#: Harbor 0.24.0 exception classes (src/harbor/{trial,environments,verifier,
#: agents/installed/base}.py) that say nothing about the agent's ability:
#: environment / verifier / provider / harness-setup failures.
INFRA_EXCEPTIONS = frozenset({
    "EnvironmentStartTimeoutError", "AgentSetupTimeoutError", "VerifierTimeoutError",
    "SandboxBuildFailedError", "HealthcheckError", "SandboxLikelyOutOfMemoryError",
    "InfrastructureTimeout", "AddTestsDirError", "VerifierOutputParseError",
    "DownloadVerifierDirError", "RewardFileNotFoundError", "RewardFileEmptyError",
    "ApiRateLimitError", "ApiUsageLimitError", "ApiInternalServerError", "ApiOverloadedError",
    "ApiConnectionClosedError", "ApiResponseStalledError", "NetworkConnectionError",
    "AgentAuthenticationError", "ModelNotFoundError", "UnsupportedModelParametersError",
    "ApiProviderResourceNotFoundError", "MemoryLimitExceededError",
})
REFUSAL_EXCEPTIONS = frozenset({"AgentSafetyRefusalError"})
TIMEOUT_EXCEPTIONS = frozenset({"AgentTimeoutError"})
_REFUSAL_RE = re.compile(r'"(?:stop_reason|finish_reason)"\s*:\s*"refusal"')


# ── one trial ────────────────────────────────────────────────────────────────


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def is_trial_result(data: Any) -> bool:
    return isinstance(data, dict) and "trial_name" in data and "task_name" in data


def _seconds(timing: Optional[dict]) -> Optional[float]:
    if not timing or not timing.get("started_at") or not timing.get("finished_at"):
        return None
    try:
        a = datetime.fromisoformat(str(timing["started_at"]).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(timing["finished_at"]).replace("Z", "+00:00"))
    except ValueError:
        return None
    return round((b - a).total_seconds(), 3)


def _reward(result: dict) -> Optional[float]:
    rewards = (result.get("verifier_result") or {}).get("rewards") or {}
    if not rewards:
        return None
    value = rewards.get("reward", next(iter(rewards.values())))
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int:
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


def step_request(metrics: dict) -> Optional[list[int]]:
    """[prompt, output, cache_read, cache_write_5m, cache_write_1h] from one ATIF
    step's metrics (prompt includes cached and written tokens), or None."""
    if not metrics or (metrics.get("prompt_tokens") is None
                       and metrics.get("completion_tokens") is None):
        return None
    extra = metrics.get("extra") or {}
    read = _int(metrics.get("cached_tokens")) or _int(extra.get("cache_read_input_tokens"))
    w5 = w1 = 0
    breakdown = extra.get("cache_creation")
    if isinstance(breakdown, dict) and breakdown:
        w5 = _int(breakdown.get("ephemeral_5m_input_tokens"))
        w1 = _int(breakdown.get("ephemeral_1h_input_tokens"))
    if not (w5 or w1):
        w5 = _int(extra.get("cache_creation_input_tokens")) or _int(
            (extra.get("prompt_tokens_details") or {}).get("cache_creation_tokens"))
    return [_int(metrics.get("prompt_tokens")), _int(metrics.get("completion_tokens")),
            read, w5, w1]


def atif_requests(trajectory: dict) -> list[list[int]]:
    out = []
    for step in trajectory.get("steps") or []:
        r = step_request(step.get("metrics") or {})
        if r is not None:
            out.append(r)
    return out


def awos_requests(rows: Iterable[dict]) -> list[list[int]]:
    return [[_int(r.get("input_tokens")), _int(r.get("output_tokens")),
             _int(r.get("cached_tokens")), _int(r.get("cache_write_tokens")), 0] for r in rows]


def _jsonl(path: Path) -> list[dict]:
    rows = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def usage(trial_dir: Path, agent_result: dict) -> tuple[str, list[list[int]]]:
    """(source, per-request rows). Per-request data is used only when its prompt
    total agrees with the agent's reported total (within 2%, so subagent calls
    missing from the main trajectory are not silently dropped); else the
    reported totals become one aggregate row priced on the short card."""
    agent_dir = trial_dir / "agent"
    reported_in = agent_result.get("n_input_tokens")
    rows: list[list[int]] = []
    source = "none"
    calls = _jsonl(agent_dir / "awos_calls.jsonl")
    if calls:
        rows, source = awos_requests(calls), "per_request"
    else:
        traj = _load(agent_dir / "trajectory.json")
        if isinstance(traj, dict):
            rows, source = atif_requests(traj), "per_request"
    if rows and reported_in:
        total = sum(r[0] for r in rows)
        if abs(total - reported_in) > 0.02 * max(reported_in, 1):
            rows = []
    if not rows and (reported_in or agent_result.get("n_output_tokens")):
        write = _int((agent_result.get("metadata") or {}).get("n_cache_write_tokens"))
        rows = [[_int(reported_in), _int(agent_result.get("n_output_tokens")),
                 _int(agent_result.get("n_cache_tokens")), write, 0]]
        source = "aggregate"
    elif not rows:
        source = "none"
    return source, rows


def uniform_cost(model: str, source: str, rows: list[list[int]]) -> Optional[float]:
    if source == "aggregate":
        cards = pricing.PRICES.get(pricing.normalize_model(model))
        if cards is None:
            return None
        (pin, pout) = cards[0]  # one request per trial is not knowable: short card
        p, o, r, w5, w1 = rows[0]
        fresh = max(0, p - r - w5 - w1)
        return (fresh * pin + r * pin * pricing.CACHE_READ_MULT
                + w5 * pin * pricing.CACHE_WRITE_5M_MULT
                + w1 * pin * pricing.CACHE_WRITE_1H_MULT + o * pout) / pricing.MTOK
    return pricing.total_cost(model, ({"prompt": p, "output": o, "cache_read": r,
                                       "cache_write_5m": w5, "cache_write_1h": w1}
                                      for p, o, r, w5, w1 in rows))


def refused(trial_dir: Path, exc_type: Optional[str]) -> bool:
    if exc_type in REFUSAL_EXCEPTIONS:
        return True
    for row in _jsonl(trial_dir / "agent" / "awos_calls.jsonl"):
        if row.get("finish_reason") == "refusal":
            return True
    for log in (trial_dir / "agent").glob("*.txt"):
        try:
            if _REFUSAL_RE.search(log.read_text(encoding="utf-8", errors="replace")):
                return True
        except OSError:
            continue
    return False


def outcome(exc_type: Optional[str], reward: Optional[float]) -> str:
    """ok | agent_timeout | infra | agent_error. A scored trial (verifier ran)
    is never infra; an agent timeout with a verifier result is scored as is."""
    if exc_type in TIMEOUT_EXCEPTIONS:
        return "agent_timeout"
    if reward is None:
        if exc_type in REFUSAL_EXCEPTIONS:
            return "agent_error"
        return "infra"  # no verifier result: environment, verifier or harness setup
    if exc_type in INFRA_EXCEPTIONS:
        return "infra"
    return "ok" if exc_type is None else "agent_error"


def summarize_trial(trial_dir: Path, result: Optional[dict] = None) -> dict:
    result = result if result is not None else _load(trial_dir / "result.json")
    info = result.get("agent_info") or {}
    model_info = info.get("model_info") or {}
    model = model_info.get("name") or ""
    agent_result = result.get("agent_result") or {}
    exc = result.get("exception_info") or {}
    exc_type = exc.get("exception_type")
    reward = _reward(result)
    source, rows = usage(trial_dir, agent_result)
    cost = uniform_cost(model, source, rows) if rows else (0.0 if source == "none" else None)
    task = str(result.get("task_name") or "")
    return {
        "agent": info.get("name"), "agent_version": info.get("version"), "model": model,
        "task": task.split("/")[-1], "task_full": task, "trial": result.get("trial_name"),
        "trial_id": str(result.get("id") or result.get("trial_name")),
        "job": trial_dir.parent.name, "reward": reward,
        "passed": bool(reward is not None and reward >= 1.0),
        "exception_type": exc_type, "exception_message": (exc.get("exception_message") or "")[:300],
        "outcome": outcome(exc_type, reward), "refusal": refused(trial_dir, exc_type),
        "agent_sec": _seconds(result.get("agent_execution")),
        "trial_sec": _seconds({"started_at": result.get("started_at"),
                               "finished_at": result.get("finished_at")}),
        "reported": {k: agent_result.get(k) for k in ("n_input_tokens", "n_cache_tokens",
                                                      "n_output_tokens", "cost_usd")},
        "usage_source": source, "requests": rows,
        "n_long_prompt_requests": sum(1 for r in rows if r[0] > pricing.LONG_PROMPT_THRESHOLD)
        if source == "per_request" else None,
        "cost_usd_uniform": None if cost is None else round(cost, 8),
        "agent_metadata": {k: (agent_result.get("metadata") or {}).get(k)
                           for k in ("stop_reason", "steps", "awos_git_sha")},
    }


def iter_trial_dirs(root: Path) -> Iterable[tuple[Path, dict]]:
    for res in sorted(root.rglob("result.json")):
        data = _load(res)
        if is_trial_result(data):
            yield res.parent, data


def spent_usd(jobs_dir: Path) -> float:
    total = 0.0
    if jobs_dir.is_dir():
        for trial_dir, data in iter_trial_dirs(jobs_dir):
            c = summarize_trial(trial_dir, data)["cost_usd_uniform"]
            total += c or 0.0
    return total


# ── stage ────────────────────────────────────────────────────────────────────


def _head_tail(src: Path, dest: Path, keep: int = LOG_KEEP_BYTES) -> None:
    data = src.read_bytes()
    if len(data) > keep:
        half = keep // 2
        data = (data[:half] + f"\n\n[... {len(data) - keep} bytes cut ...]\n\n".encode()
                + data[-half:])
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def stage(jobs_dir: Path, dest: Path) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    for trial_dir, data in iter_trial_dirs(jobs_dir):
        rel = trial_dir.relative_to(jobs_dir)
        out = dest / "trials" / rel
        out.mkdir(parents=True, exist_ok=True)
        (out / SUMMARY).write_text(json.dumps(summarize_trial(trial_dir, data), indent=1),
                                   encoding="utf-8")
        shutil.copy2(trial_dir / "result.json", out / "result.json")
        for name in ("exception.txt", "verifier/reward.txt", "verifier/reward.json",
                     "verifier/test-stdout.txt", "agent/awos_result.json",
                     "agent/awos_calls.jsonl"):
            if (trial_dir / name).is_file():
                _head_tail(trial_dir / name, out / name)
        for log in list((trial_dir / "agent").glob("*.txt")) + list(
                (trial_dir / "agent").glob("*.log")):
            if log.is_file():
                _head_tail(log, out / "agent" / log.name)
        n += 1
    for extra in [jobs_dir / "shard.json", *jobs_dir.glob("*/result.json")]:
        if extra.is_file() and not is_trial_result(_load(extra)):
            target = dest / extra.relative_to(jobs_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(extra, target)
    print(f"[harbor_collect] staged {n} trial(s) from {jobs_dir} -> {dest}")
    return n


# ── statistics ───────────────────────────────────────────────────────────────


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p: binomial test of min(b, c) in b + c at 1/2."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def paired_bootstrap(diffs: list[float], iters: int = 10_000, seed: int = 0) -> dict:
    """Mean of per-task differences with a 95% percentile CI over tasks."""
    if not diffs:
        return {"mean": float("nan"), "ci": (float("nan"), float("nan")), "p_le_0": float("nan")}
    rng = random.Random(seed)
    n = len(diffs)
    means = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(iters))
    lo, hi = means[int(0.025 * iters)], means[int(0.975 * iters) - 1]
    mean = sum(diffs) / n
    # two-sided: how often the resampled mean falls on the other side of 0
    side = sum(1 for m in means if (m <= 0 if mean > 0 else m >= 0)) / iters
    return {"mean": mean, "ci": (lo, hi), "p_two_sided": min(1.0, 2 * side)}


# ── report ───────────────────────────────────────────────────────────────────


def load_summaries(paths: list[Path]) -> list[dict]:
    seen: dict[str, dict] = {}
    for root in paths:
        for f in sorted(root.rglob(SUMMARY)):
            s = _load(f)
            if isinstance(s, dict) and s.get("trial_id"):
                seen.setdefault(s["trial_id"], s)
        for trial_dir, data in iter_trial_dirs(root):
            if (trial_dir / SUMMARY).is_file():
                continue
            s = summarize_trial(trial_dir, data)
            seen.setdefault(s["trial_id"], s)
    return list(seen.values())


def _mean(xs: list[Optional[float]]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def agent_table(trials: list[dict]) -> list[dict]:
    rows = []
    for agent in sorted({t["agent"] for t in trials}):
        ts = [t for t in trials if t["agent"] == agent]
        n, k = len(ts), sum(t["passed"] for t in ts)
        scored = [t for t in ts if t["outcome"] != "infra"]
        ks = sum(t["passed"] for t in scored)
        rows.append({
            "agent": agent, "versions": sorted({str(t.get("agent_version")) for t in ts}),
            "trials": n, "tasks": len({t["task"] for t in ts}), "passed": k,
            "pass_rate": k / n if n else float("nan"), "ci95": wilson(k, n),
            "pass_rate_excl_infra": ks / len(scored) if scored else float("nan"),
            "ci95_excl_infra": wilson(ks, len(scored)),
            "mean_cost_usd": _mean([t["cost_usd_uniform"] for t in ts]),
            "total_cost_usd": sum(t["cost_usd_uniform"] or 0 for t in ts),
            "unpriced_trials": sum(1 for t in ts if t["cost_usd_uniform"] is None),
            "reported_cost_usd": sum((t["reported"] or {}).get("cost_usd") or 0 for t in ts),
            "mean_agent_sec": _mean([t["agent_sec"] for t in ts]),
            "infra_errors": sum(t["outcome"] == "infra" for t in ts),
            "agent_errors": sum(t["outcome"] == "agent_error" for t in ts),
            "agent_timeouts": sum(t["outcome"] == "agent_timeout" for t in ts),
            "refusals": sum(bool(t["refusal"]) for t in ts),
            "usage_sources": {s: sum(t["usage_source"] == s for t in ts)
                              for s in ("per_request", "aggregate", "none")},
            "long_prompt_requests": sum(t.get("n_long_prompt_requests") or 0 for t in ts),
        })
    return rows


def paired(trials: list[dict], base: str = AWOS) -> list[dict]:
    by: dict[tuple[str, str], list[dict]] = {}
    for t in trials:
        by.setdefault((t["agent"], t["task"]), []).append(t)
    out = []
    others = sorted({t["agent"] for t in trials} - {base})
    for other in others:
        tasks = sorted({task for (a, task) in by if a == base}
                       & {task for (a, task) in by if a == other})
        if not tasks:
            continue
        k1 = all(len(by[(base, t)]) == 1 and len(by[(other, t)]) == 1 for t in tasks)
        row: dict[str, Any] = {"base": base, "other": other, "tasks": len(tasks)}
        rate = {a: [sum(x["passed"] for x in by[(a, t)]) / len(by[(a, t)]) for t in tasks]
                for a in (base, other)}
        cost = {a: [_mean([x["cost_usd_uniform"] for x in by[(a, t)]]) for t in tasks]
                for a in (base, other)}
        row["base_rate"] = sum(rate[base]) / len(tasks)
        row["other_rate"] = sum(rate[other]) / len(tasks)
        if k1:
            b = sum(1 for i in range(len(tasks)) if rate[base][i] == 1 and rate[other][i] == 0)
            c = sum(1 for i in range(len(tasks)) if rate[base][i] == 0 and rate[other][i] == 1)
            row.update(test="exact McNemar (k=1)", base_only=b, other_only=c,
                       p_value=mcnemar_exact(b, c))
        else:
            bs = paired_bootstrap([rate[base][i] - rate[other][i] for i in range(len(tasks))])
            row.update(test="paired bootstrap over tasks (k>1)", diff=bs["mean"],
                       diff_ci95=bs["ci"], p_value=bs.get("p_two_sided"))
        cd = [cost[base][i] - cost[other][i] for i in range(len(tasks))
              if cost[base][i] is not None and cost[other][i] is not None]
        cb = paired_bootstrap(cd, seed=1)
        row.update(cost_diff_per_task=cb["mean"], cost_diff_ci95=cb["ci"],
                   cost_ratio=(_mean([c for c in cost[base] if c is not None]) or 0)
                   / (_mean([c for c in cost[other] if c is not None]) or float("nan")))
        out.append(row)
    return out


def _f(x: Optional[float], fmt: str = "{:.3f}") -> str:
    return "-" if x is None or (isinstance(x, float) and math.isnan(x)) else fmt.format(x)


def render(table: list[dict], pairs: list[dict]) -> str:
    lines = ["| agent | trials | tasks | pass | rate [95% CI] | rate excl. infra | "
             "mean $/trial | total $ | mean agent s | infra | timeouts | refusals | usage src |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in table:
        src = ",".join(f"{k}:{v}" for k, v in r["usage_sources"].items() if v)
        lines.append(
            f"| {r['agent']} | {r['trials']} | {r['tasks']} | {r['passed']} | "
            f"{_f(r['pass_rate'])} [{_f(r['ci95'][0])}, {_f(r['ci95'][1])}] | "
            f"{_f(r['pass_rate_excl_infra'])} | {_f(r['mean_cost_usd'], '{:.4f}')} | "
            f"{_f(r['total_cost_usd'], '{:.4f}')} | {_f(r['mean_agent_sec'], '{:.0f}')} | "
            f"{r['infra_errors']} | {r['agent_timeouts']} | {r['refusals']} | {src} |")
    if pairs:
        lines += ["", "| AWOS vs | tasks | AWOS | other | test | detail | p | cost diff $/task [95% CI] | cost ratio |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for p in pairs:
            detail = (f"AWOS-only {p['base_only']}, other-only {p['other_only']}"
                      if "base_only" in p else
                      f"diff {_f(p['diff'])} [{_f(p['diff_ci95'][0])}, {_f(p['diff_ci95'][1])}]")
            lines.append(
                f"| {p['other']} | {p['tasks']} | {_f(p['base_rate'])} | {_f(p['other_rate'])} | "
                f"{p['test']} | {detail} | {_f(p['p_value'], '{:.4f}')} | "
                f"{_f(p['cost_diff_per_task'], '{:+.4f}')} [{_f(p['cost_diff_ci95'][0], '{:+.4f}')}, "
                f"{_f(p['cost_diff_ci95'][1], '{:+.4f}')}] | {_f(p['cost_ratio'], 'x{:.2f}')} |")
    lines += ["", "Costs: uniform Haiku 5.5 prices per request (harbor_agents/pricing.py); "
              "'aggregate' usage rows are priced on the short (<=100K) card."]
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stage")
    s.add_argument("--jobs-dir", required=True)
    s.add_argument("--dest", required=True)
    r = sub.add_parser("report")
    r.add_argument("paths", nargs="+")
    r.add_argument("--json", default=None)
    r.add_argument("--base", default=AWOS)
    a = p.parse_args(argv)
    if a.cmd == "stage":
        stage(Path(a.jobs_dir), Path(a.dest))
        return 0
    trials = load_summaries([Path(x).expanduser() for x in a.paths])
    if not trials:
        print("[harbor_collect] no trials found", file=sys.stderr)
        return 1
    table, pairs = agent_table(trials), paired(trials, a.base)
    print(render(table, pairs))
    if a.json:
        Path(a.json).write_text(json.dumps({"agents": table, "paired": pairs,
                                            "trials": trials}, indent=1, default=str),
                                encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
