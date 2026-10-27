"""
teacher_cost.py — what N teacher episodes cost, from the prompt sizes of existing live-web runs.

    python -m scaffold.rl.web.teacher_cost --runs base=runs/web/qwen4b-dev/ --runs afinal=runs/web/afinal-dev/ \\
        [--episodes 300 --episodes 1000] [--model claude-haiku-5-5] [--chars-per-token 3.2] [--repair-rate 0.1]

Reads trajectories.jsonl (steps[].system / user / reply; never prints task text) and reports, per run:
the share of episodes that end before the first model call (blocked start page: $0), steps per episode that
reached the model, input characters per step (system + the teacher's format rule + user), reply characters.
Per call: input tokens = chars / chars_per_token, output tokens = reply chars / chars_per_token + 10; calls per
step = 1 + repair_rate (a repair call resends the prompt plus the rejected reply). Prices: teacher.PRICES,
no cache hits assumed (the system block is at Claude Haiku 5.5's 512-token cache minimum).

The worst case is the hard cap's view: every episode reaches the model, runs all MAX_STEPS steps on the p95
prompt, and every step needs a repair call with the full max_tokens out.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics as st
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from scaffold.rl.harness.models import _FORMAT_RULE

from .source import MAX_STEPS
from .teacher import DEFAULT_TEACHER, MAX_TOKENS, PRICES


def load(path: Path) -> List[dict]:
    p = Path(path)
    files = sorted(p.rglob("trajectories.jsonl")) if p.is_dir() else [p]
    latest: Dict[str, dict] = {}
    for f in files:
        for ln in f.read_text().splitlines():
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            latest[r["task"]["id"]] = r
    return [r for r in latest.values() if not (r.get("trajectory") or {}).get("error")]


def profile(recs: Sequence[dict]) -> Dict[str, Any]:
    """Aggregates only (no text)."""
    n = len(recs)
    stepped = [r["trajectory"]["steps"] for r in recs if r["trajectory"].get("steps")]
    steps = [s for ss in stepped for s in ss]
    if not steps:
        return {"episodes": n, "reach_model": 0}
    ins = sorted(len(s["system"]) + len(_FORMAT_RULE) + len(s["user"]) for s in steps)
    return {"episodes": n, "reach_model": len(stepped), "reach_rate": round(len(stepped) / n, 3),
            "steps_per_reaching_episode": round(len(steps) / len(stepped), 2),
            "steps_p90": sorted(len(x) for x in stepped)[int(0.9 * (len(stepped) - 1))],
            "in_chars_mean": round(st.mean(ins)), "in_chars_p95": ins[int(0.95 * (len(ins) - 1))],
            "reply_chars_mean": round(st.mean(len(s.get("reply") or "") for s in steps), 1)}


def episode_usd(prof: Dict[str, Any], model: str = DEFAULT_TEACHER, chars_per_token: float = 3.2,
                repair_rate: float = 0.1) -> Dict[str, float]:
    pin, _w, _r, pout = PRICES[model]
    tin = prof["in_chars_mean"] / chars_per_token
    tout = prof["reply_chars_mean"] / chars_per_token + 10
    calls = prof["reach_rate"] * prof["steps_per_reaching_episode"] * (1 + repair_rate)
    usd = calls * (tin * pin + tout * pout) / 1e6
    return {"calls": round(calls, 2), "in_tokens": round(calls * tin), "out_tokens": round(calls * tout),
            "usd": usd}


def worst_episode_usd(prof: Dict[str, Any], model: str = DEFAULT_TEACHER, chars_per_token: float = 3.2,
                      max_steps: int = MAX_STEPS, max_tokens: int = MAX_TOKENS) -> float:
    pin, _w, _r, pout = PRICES[model]
    tin = math.ceil(prof["in_chars_p95"] / chars_per_token)
    return max_steps * 2 * (tin * pin + max_tokens * pout) / 1e6


def table(profiles: Dict[str, Dict[str, Any]], episodes: Sequence[int], model: str, chars_per_token: float,
          repair_rate: float) -> str:
    lines = [f"| profile ({model}) | reach model | steps/ep | input tok/call | calls/ep | $/episode | "
             + " | ".join(f"${n} eps" for n in episodes) + " |",
             "|---|---|---|---|---|---|" + "---|" * len(episodes)]
    for label, p in profiles.items():
        if not p.get("reach_model"):
            continue
        e = episode_usd(p, model, chars_per_token, repair_rate)
        lines.append(f"| {label} | {p['reach_rate']:.0%} | {p['steps_per_reaching_episode']} | "
                     f"{p['in_chars_mean'] / chars_per_token:.0f} | {e['calls']} | {e['usd']:.5f} | "
                     + " | ".join(f"{e['usd'] * n:.2f}" for n in episodes) + " |")
        w = worst_episode_usd(p, model, chars_per_token)
        lines.append(f"| {label} worst case (cap view) | 100% | {MAX_STEPS} | {p['in_chars_p95'] / chars_per_token:.0f} | "
                     f"{2 * MAX_STEPS} | {w:.5f} | " + " | ".join(f"{w * n:.2f}" for n in episodes) + " |")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scaffold.rl.web.teacher_cost", description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs", action="append", required=True, help="LABEL=PATH (run dir or trajectories.jsonl)")
    ap.add_argument("--episodes", action="append", type=int, default=None)
    ap.add_argument("--model", default=DEFAULT_TEACHER, choices=sorted(PRICES))
    ap.add_argument("--chars-per-token", type=float, default=3.2)
    ap.add_argument("--repair-rate", type=float, default=0.1)
    a = ap.parse_args(argv)
    profs = {}
    for spec in a.runs:
        label, _, path = spec.partition("=")
        profs[label] = profile(load(Path(path)))
    print(json.dumps(profs, indent=1))
    print(table(profs, a.episodes or [300, 1000], a.model, a.chars_per_token, a.repair_rate))
    return 0


if __name__ == "__main__":
    sys.exit(main())
