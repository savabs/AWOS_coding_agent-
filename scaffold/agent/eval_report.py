"""
eval_report.py — AWOS System Health & Impact Dashboard.

Reads real execution data from .awos/spans.jsonl and produces a
beautiful terminal report showing what's working, what's improving,
and where there's still room to grow.

Run:
    python3 -m scaffold.agent.eval_report
    python3 -m scaffold.agent.eval_report --window 50    # last 50 tasks
    python3 -m scaffold.agent.eval_report --json          # machine-readable output
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

# ── ANSI colours (no deps) ────────────────────────────────────────────────────

R  = "\033[31m"   # red
G  = "\033[32m"   # green
Y  = "\033[33m"   # yellow
B  = "\033[34m"   # blue
M  = "\033[35m"   # magenta
C  = "\033[36m"   # cyan
W  = "\033[97m"   # bright white
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

def _g(text): return f"{G}{text}{RESET}"
def _r(text): return f"{R}{text}{RESET}"
def _y(text): return f"{Y}{text}{RESET}"
def _b(text): return f"{B}{text}{RESET}"
def _c(text): return f"{C}{text}{RESET}"
def _m(text): return f"{M}{text}{RESET}"
def _bold(text): return f"{BOLD}{text}{RESET}"
def _dim(text): return f"{DIM}{text}{RESET}"

def _ok(label):     return f"  {_g('✔')}  {label}"
def _warn(label):   return f"  {_y('⚠')}  {label}"
def _off(label):    return f"  {_r('✘')}  {label}"
def _dot(label):    return f"  {_dim('·')}  {label}"

def _bar(value: float, width: int = 30, colour=G) -> str:
    filled = int(round(value * width))
    bar = "█" * filled + "░" * (width - filled)
    return f"{colour}{bar}{RESET} {value * 100:5.1f}%"


# ── Span loader ───────────────────────────────────────────────────────────────

@dataclass
class Span:
    task_id: str
    action: str
    file: str
    model_chosen: str
    escalation_level: int
    strategy: str
    success: bool
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_sec: float
    attempt: int
    cached_tokens: int


def _load_spans(base_dir: str, window: int) -> List[Span]:
    path = Path(base_dir) / "spans.jsonl"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    if window:
        lines = lines[-window:]
    spans = []
    for line in lines:
        try:
            d = json.loads(line)
            spans.append(Span(
                task_id=str(d.get("task_id", "")),
                action=d.get("action", ""),
                file=d.get("file", ""),
                model_chosen=d.get("model_chosen", "unknown"),
                escalation_level=int(d.get("escalation_level", 1)),
                strategy=d.get("strategy", ""),
                success=bool(d.get("success", False)),
                tokens_in=int(d.get("tokens_in", 0)),
                tokens_out=int(d.get("tokens_out", 0)),
                cost_usd=float(d.get("cost_usd", 0.0)),
                latency_sec=float(d.get("latency_sec", 0.0)),
                attempt=int(d.get("attempt", 1)),
                cached_tokens=int(d.get("cached_tokens", 0)),
            ))
        except Exception:
            continue
    return spans


# ── Skill store loader ────────────────────────────────────────────────────────

def _load_skills(base_dir: str) -> int:
    skills_dir = Path(base_dir) / "skills"
    if not skills_dir.exists():
        return 0
    return len(list(skills_dir.glob("*.md")))


def _load_mcts_traces(base_dir: str) -> int:
    p = Path(base_dir) / "mcts_traces.jsonl"
    if not p.exists():
        return 0
    return sum(1 for _ in p.open())


# ── Feature status ────────────────────────────────────────────────────────────

@dataclass
class FeatureStatus:
    enabled: bool
    name: str
    env_var: str
    description: str
    impact: str


def _feature_status() -> List[FeatureStatus]:
    return [
        FeatureStatus(
            enabled=True,  # always on
            name="Planner Singleton (P2.1)",
            env_var="(always on)",
            description="Haiku for simple goals, Sonnet for complex",
            impact="~60% cheaper planning"
        ),
        FeatureStatus(
            enabled=True,  # always on
            name="Fault Localizer (P1.5)",
            env_var="(always on)",
            description="Hierarchical fault localization narrows context",
            impact="60-80% context token reduction"
        ),
        FeatureStatus(
            enabled=bool(os.getenv("AWOS_PARALLEL_SAMPLING")),
            name="Parallel Sampling + Majority Vote (P2.2)",
            env_var="AWOS_PARALLEL_SAMPLING=true",
            description="2-3 diverse patch candidates, AST-normalized vote",
            impact="+15-20% first-attempt pass rate"
        ),
        FeatureStatus(
            enabled=bool(os.getenv("AWOS_USE_WORKTREE")),
            name="Git Worktree Isolation (P2.3)",
            env_var="AWOS_USE_WORKTREE=true",
            description="Each feature runs in isolated git branch",
            impact="Safe parallel execution, clean diffs"
        ),
        FeatureStatus(
            enabled=bool(os.getenv("AWOS_SAFE_TO_RUN_TESTS")),
            name="Test Execution Gate (P1.1)",
            env_var="AWOS_SAFE_TO_RUN_TESTS=1",
            description="pytest after each write, feedback retry loop",
            impact="+25% verified correctness"
        ),
        FeatureStatus(
            enabled=bool(os.getenv("AWOS_MCTS")),
            name="MCTS Patch Search (P4)",
            env_var="AWOS_MCTS=1",
            description="Tree search over patch space (hard bugs only)",
            impact="Finds solutions single-shot misses"
        ),
    ]


# ── Metric computations ───────────────────────────────────────────────────────

def _percentile(data: List[float], p: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    idx = (len(s) - 1) * p / 100
    lo, hi = int(idx), min(int(idx) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)


def _analyse(spans: List[Span]) -> dict:
    if not spans:
        return {}
    n = len(spans)
    successes = sum(1 for s in spans if s.success)
    costs = [s.cost_usd for s in spans]
    latencies = [s.latency_sec for s in spans if s.latency_sec > 0]
    tokens_in = [s.tokens_in for s in spans if s.tokens_in > 0]
    cached = [s.cached_tokens for s in spans if s.tokens_in > 0]
    model_dist = defaultdict(int)
    for s in spans:
        model_dist[s.model_chosen.split("-")[0] if s.model_chosen else "?"] += 1
    first_attempt = sum(1 for s in spans if s.attempt == 1 and s.success)
    multi_attempt = sum(1 for s in spans if s.attempt > 1)
    cache_ratio = (sum(cached) / sum(tokens_in)) if sum(tokens_in) > 0 else 0.0
    return {
        "n": n,
        "success_rate": successes / n,
        "first_attempt_rate": first_attempt / n,
        "total_cost_usd": sum(costs),
        "avg_cost_usd": sum(costs) / n,
        "p50_latency": _percentile(latencies, 50),
        "p95_latency": _percentile(latencies, 95),
        "avg_tokens_in": sum(tokens_in) / len(tokens_in) if tokens_in else 0,
        "cache_hit_ratio": cache_ratio,
        "multi_attempt_rate": multi_attempt / n,
        "model_dist": dict(model_dist),
    }


def _score_health(metrics: dict, features: List[FeatureStatus]) -> tuple[float, str]:
    """Return (0-1 health score, colour)."""
    if not metrics:
        return 0.0, Y
    score = 0.0
    score += metrics.get("success_rate", 0) * 0.4
    score += metrics.get("first_attempt_rate", 0) * 0.2
    score += metrics.get("cache_hit_ratio", 0) * 0.1
    score += sum(1 for f in features if f.enabled) / len(features) * 0.2
    score += max(0, 1 - metrics.get("avg_cost_usd", 0.05) / 0.10) * 0.1
    colour = G if score >= 0.7 else (Y if score >= 0.45 else R)
    return min(score, 1.0), colour


# ── Render ────────────────────────────────────────────────────────────────────

HEADER = f"""
{B}╔══════════════════════════════════════════════════════════════════╗
║{W}  AWOS SOTA — System Health & Impact Dashboard                    {B}║
╚══════════════════════════════════════════════════════════════════╝{RESET}
"""

def _render(metrics: dict, features: List[FeatureStatus],
            skills: int, mcts_traces: int, base_dir: str, window: int):
    print(HEADER)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"  {_dim('Generated:')} {now}   {_dim('Data window:')} last {window} tasks\n")

    # ── Feature Status ─────────────────────────────────────────────────────
    print(f"{_bold('─── Feature Activation ──────────────────────────────────────────')}")
    for f in features:
        if f.enabled:
            print(_ok(f"{_bold(f.name)}  {_dim(f.impact)}"))
        else:
            print(_off(f"{f.name}  {_dim('→ set ' + f.env_var + ' to enable')}"))
    print()

    # ── Performance Metrics ────────────────────────────────────────────────
    print(f"{_bold('─── Execution Metrics ───────────────────────────────────────────')}")
    if not metrics:
        print(f"  {_y('No spans data yet')} — run some tasks first, then re-check.")
        print(f"  {_dim('Data path: ' + base_dir + '/spans.jsonl')}\n")
    else:
        n = metrics["n"]
        sr = metrics["success_rate"]
        far = metrics["first_attempt_rate"]
        chr_ = metrics["cache_hit_ratio"]
        mar = metrics["multi_attempt_rate"]
        ac = metrics["avg_cost_usd"]
        at = metrics["avg_tokens_in"]
        p50 = metrics["p50_latency"]
        p95 = metrics["p95_latency"]

        print(f"  Tasks analysed:       {_bold(str(n))}")
        print()
        print(f"  Success rate          {_bar(sr, 28, G if sr >= 0.8 else Y)}")
        print(f"  First-attempt success {_bar(far, 28, G if far >= 0.7 else Y)}")
        print(f"  Prompt cache hits     {_bar(chr_, 28, G if chr_ >= 0.5 else Y)}")
        print()
        sr_col = G if sr >= 0.8 else (Y if sr >= 0.5 else R)
        print(f"  Avg cost/task:   {sr_col}${ac:.4f}{RESET}   (target <$0.05)")
        print(f"  Avg input tokens: {at:,.0f}")
        print(f"  p50 latency:     {p50:.1f}s    p95: {p95:.1f}s")
        print(f"  Multi-attempt:   {mar*100:.1f}% of tasks needed >1 attempt")
        print()
        print(f"  Model distribution:")
        for model, count in sorted(metrics["model_dist"].items(), key=lambda x: -x[1]):
            pct = count / n
            bar_col = C if "haiku" in model.lower() or "deepseek" in model.lower() else M
            print(f"    {model:15s}  {_bar(pct, 20, bar_col)}")
        print()

    # ── Knowledge Store ────────────────────────────────────────────────────
    print(f"{_bold('─── Knowledge Store ─────────────────────────────────────────────')}")
    skill_col = G if skills >= 10 else (Y if skills >= 1 else R)
    mcts_col  = G if mcts_traces >= 500 else (Y if mcts_traces >= 50 else R)
    prm_ready = Path(base_dir + "/prm_weights.pkl").exists()

    print(f"  Skills learned:    {skill_col}{skills:3d}{RESET}  {'▲ growing knowledge base' if skills > 0 else _dim('→ skills auto-extract from successful tasks')}")
    print(f"  MCTS traces:       {mcts_col}{mcts_traces:4d}{RESET}  {'PRM can train at 500+' if mcts_traces < 500 else _g('PRM training threshold reached')}")
    print(f"  PRM weights:       {'  ' + _g('loaded — fast oracle active') if prm_ready else '  ' + _dim('not trained yet')}")
    print()

    # ── Health Score ───────────────────────────────────────────────────────
    print(f"{_bold('─── Overall Health Score ────────────────────────────────────────')}")
    health, hcol = _score_health(metrics, features)
    grade = "A" if health >= 0.85 else ("B" if health >= 0.70 else ("C" if health >= 0.50 else "D"))
    grade_msg = {
        "A": "Excellent — the system is firing on all cylinders.",
        "B": "Good — enable remaining features for full performance.",
        "C": "Fair — activate parallel sampling and test execution.",
        "D": "Limited — most features are still gated off.",
    }[grade]
    print(f"  {_bar(health, 40, hcol)}")
    print(f"  Grade: {hcol}{_bold(grade)}{RESET}  —  {grade_msg}")
    print()

    # ── Next Recommendations ───────────────────────────────────────────────
    disabled = [f for f in features if not f.enabled]
    if disabled:
        print(f"{_bold('─── Unlock More Performance ─────────────────────────────────────')}")
        for f in disabled[:3]:
            print(f"  {_c('→')} export {f.env_var}")
            print(f"     {_dim(f.description)}  ({_y(f.impact)})")
        print()

    # ── Feature Impact Summary (theoretical) ──────────────────────────────
    print(f"{_bold('─── Theoretical Impact of Your SOTA Stack ───────────────────────')}")
    rows = [
        ("P2.1  Planner Haiku routing",  "−60% planning cost",    True),
        ("P1.5  Fault localization",     "−70% context tokens",   True),
        ("P2.2  Parallel sampling",      "+18% pass@1 rate",      bool(os.getenv("AWOS_PARALLEL_SAMPLING"))),
        ("P3.1  Lint feedback loop",     "−40% retry turns",      True),
        ("P3.2  Skill library",          "+12% repeated task wins", skills > 0),
        ("P4    MCTS search",            "Solves hard bugs",       bool(os.getenv("AWOS_MCTS"))),
        ("P5    Process Reward Model",   "Free MCTS oracle",       prm_ready),
    ]
    for name, impact, active in rows:
        icon = _g("✔") if active else _dim("○")
        col  = G if active else DIM
        print(f"  {icon}  {col}{name:<35}{RESET}  {_y(impact) if active else _dim(impact)}")
    print()

    # ── P6: Self-Learning Intelligence ────────────────────────────────────
    try:
        from .learning_inspector import LearningInspector
        _insp = LearningInspector(awos_dir=base_dir)
        _lr = _insp.report()
        _v = _lr.velocity
        trend_icon = {"improving": "↑", "stable": "→", "regressing": "↓"}.get(
            _v.improvement_trend, "?"
        )
        trend_col = G if _v.improvement_trend == "improving" else (
            Y if _v.improvement_trend == "stable" else R
        )
        print(f"{_bold('─── Self-Learning Intelligence (P6) ────────────────────────────')}")
        print(f"  Trend     {trend_col}{trend_icon} {_v.improvement_trend}{RESET}  "
              f"({_v.episodes_total} episodes · {_v.linucb_updates} LinUCB updates)")
        print(f"  GP World Model   {'  ' + _g('active') if _v.gp_fitted else '  ' + _dim('warming up')}"
              f"    PRM   {'  ' + _g('active') if _v.prm_ready else '  ' + _dim('dormant')}")
        if _lr.curriculum:
            top = _lr.curriculum[0]
            print(f"  Next focus  {_y(top.task_type)}  —  {_dim(top.suggested_action[:65])}")
        print(f"  {_dim('Run: python3 -m scaffold.agent.learning_inspector  for full detail')}")
        print()
    except Exception:
        pass

    print(f"{_dim('─────────────────────────────────────────────────────────────────')}")
    print(f"  {_dim('Spans file:')} {base_dir}/spans.jsonl")
    print(f"  {_dim('Skills dir:')} {base_dir}/skills/")
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AWOS health dashboard")
    parser.add_argument("--window", type=int, default=100, help="Last N spans to analyse")
    parser.add_argument("--dir", type=str, default=".awos", help="AWOS data directory")
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    args = parser.parse_args()

    base_dir = args.dir
    spans = _load_spans(base_dir, args.window)
    metrics = _analyse(spans)
    features = _feature_status()
    skills = _load_skills(base_dir)
    mcts_traces = _load_mcts_traces(base_dir)

    if args.json:
        health, _ = _score_health(metrics, features)
        print(json.dumps({
            "metrics": metrics,
            "features": [{"name": f.name, "enabled": f.enabled} for f in features],
            "skills": skills,
            "mcts_traces": mcts_traces,
            "health_score": round(health, 3),
        }, indent=2))
        return

    _render(metrics, features, skills, mcts_traces, base_dir, args.window)


if __name__ == "__main__":
    main()
