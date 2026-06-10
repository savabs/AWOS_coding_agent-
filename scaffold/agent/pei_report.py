"""
PEI Report — client-facing Project Efficiency Index scorecard.

Reads .awos/ runtime data and produces a proof-of-value report:
  Quality × Speed ÷ Tokens  (cost derived from token counts)

Usage:
    from pei_report import PEIReport
    report = PEIReport(store_path=".awos", project_name="My Repo")
    report.print_summary()
    report.write_html("reports/pei_report.html")
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_div(num: float, den: float, default: float = 0.0) -> float:
    return num / den if den else default


# Naive baselines for comparison (industry typical routing)
_BASELINE_SONNET_COST = 0.050   # $/task if always Claude Sonnet
_BASELINE_COPILOT_COST = 0.020  # $/task Copilot-backend equivalent


@dataclass
class PEISnapshot:
    """Point-in-time project efficiency metrics."""

    captured_at: str = field(default_factory=_now_iso)
    project_name: str = "Project"

    # Quality
    task_success_rate: float = 0.0
    goal_success_rate: float = 0.0
    goals_complete: int = 0
    goals_failed: int = 0
    goals_total: int = 0
    total_tasks: int = 0

    # Speed
    avg_latency_sec: float = 0.0
    median_latency_sec: float = 0.0
    p95_latency_sec: float = 0.0

    # Tokens (primary measure — from API responses)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    avg_tokens_per_task: float = 0.0
    tokens_by_model: dict[str, int] = field(default_factory=dict)

    # Cost (derived from tokens × price table, secondary)
    total_cost_usd: float = 0.0
    avg_cost_per_task_usd: float = 0.0
    cache_hits: int = 0
    cache_savings_usd: float = 0.0
    monthly_budget_usd: float = 20.0
    budget_used_pct: float = 0.0

    # Model mix (proof of routing)
    model_mix: dict[str, int] = field(default_factory=dict)
    cheap_tier_pct: float = 0.0  # % tasks on tier 0–1

    # Learning state
    error_patterns: int = 0
    skills_count: int = 0
    tools_synthesized: int = 0
    prompt_evolved: bool = False
    learning_sessions: int = 0

    # PEI + comparisons
    pei_score: float = 0.0
    sonnet_baseline_cost_usd: float = 0.0
    copilot_baseline_cost_usd: float = 0.0
    savings_vs_sonnet_pct: float = 0.0
    savings_vs_copilot_pct: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "captured_at": self.captured_at,
            "project_name": self.project_name,
            "quality": {
                "task_success_rate": round(self.task_success_rate, 4),
                "goal_success_rate": round(self.goal_success_rate, 4),
                "goals_complete": self.goals_complete,
                "goals_failed": self.goals_failed,
                "goals_total": self.goals_total,
            },
            "speed": {
                "avg_latency_sec": round(self.avg_latency_sec, 3),
                "median_latency_sec": round(self.median_latency_sec, 3),
                "p95_latency_sec": round(self.p95_latency_sec, 3),
            },
            "tokens": {
                "total_input": self.total_input_tokens,
                "total_output": self.total_output_tokens,
                "total": self.total_tokens,
                "avg_per_task": round(self.avg_tokens_per_task, 1),
                "by_model": self.tokens_by_model,
            },
            "cost": {
                "total_cost_usd": round(self.total_cost_usd, 4),
                "avg_cost_per_task_usd": round(self.avg_cost_per_task_usd, 6),
                "cache_hits": self.cache_hits,
                "cache_savings_usd": round(self.cache_savings_usd, 4),
                "budget_used_pct": round(self.budget_used_pct, 1),
            },
            "model_mix": self.model_mix,
            "cheap_tier_pct": round(self.cheap_tier_pct, 1),
            "learning": {
                "error_patterns": self.error_patterns,
                "skills_count": self.skills_count,
                "tools_synthesized": self.tools_synthesized,
                "prompt_evolved": self.prompt_evolved,
            },
            "pei_score": round(self.pei_score, 2),
            "baselines": {
                "sonnet_cost_usd": round(self.sonnet_baseline_cost_usd, 2),
                "copilot_cost_usd": round(self.copilot_baseline_cost_usd, 2),
                "savings_vs_sonnet_pct": round(self.savings_vs_sonnet_pct, 1),
                "savings_vs_copilot_pct": round(self.savings_vs_copilot_pct, 1),
            },
        }


class PEIReport:
    """Generate client-facing PEI scorecards from .awos/ data."""

    _CHEAP_MODELS = {
        "DeepSeek V4 Flash",
        "Gemini 2.5 Flash-Lite",
        "gemini_flash",
        "deepseek",
    }

    def __init__(
        self,
        store_path: str = ".awos",
        project_name: str = "Project",
        monthly_budget: float = 20.0,
        window: Optional[int] = None,
    ) -> None:
        self._store = Path(store_path)
        self._project_name = project_name
        self._monthly_budget = monthly_budget
        self._window = window

    def snapshot(self) -> PEISnapshot:
        snap = PEISnapshot(
            project_name=self._project_name,
            monthly_budget_usd=self._monthly_budget,
        )

        spans = self._load_spans()
        goals = self._load_goals()
        self._fill_quality(snap, spans, goals)
        self._fill_speed(snap, spans)
        self._fill_tokens(snap, spans)
        self._fill_cost(snap, spans)
        self._fill_model_mix(snap, spans)
        self._fill_learning(snap)
        self._fill_pei(snap)
        return snap

    # ── Data loaders ──────────────────────────────────────────────────────

    def _load_spans(self) -> list[dict[str, Any]]:
        path = self._store / "spans.jsonl"
        if not path.exists():
            return []
        spans: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                spans.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if self._window and len(spans) > self._window:
            spans = spans[-self._window :]
        return spans

    def _load_goals(self) -> list[dict[str, Any]]:
        state_dir = self._store / "state"
        if not state_dir.exists():
            return []
        goals: list[dict[str, Any]] = []
        for f in state_dir.glob("*.json"):
            try:
                goals.append(json.loads(f.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
        return goals

    # ── Metric fill ───────────────────────────────────────────────────────

    def _fill_quality(
        self,
        snap: PEISnapshot,
        spans: list[dict[str, Any]],
        goals: list[dict[str, Any]],
    ) -> None:
        snap.total_tasks = len(spans)
        if spans:
            successes = sum(1 for s in spans if s.get("success"))
            snap.task_success_rate = successes / len(spans)

        snap.goals_total = len(goals)
        snap.goals_complete = sum(1 for g in goals if g.get("status") == "complete")
        snap.goals_failed = sum(1 for g in goals if g.get("status") == "failed")
        if goals:
            snap.goal_success_rate = snap.goals_complete / len(goals)

    def _fill_speed(self, snap: PEISnapshot, spans: list[dict[str, Any]]) -> None:
        latencies_ms = [
            s["total_latency_ms"]
            for s in spans
            if s.get("total_latency_ms") is not None and s["total_latency_ms"] > 0
        ]
        if not latencies_ms:
            return
        latencies_ms.sort()
        snap.avg_latency_sec = sum(latencies_ms) / len(latencies_ms) / 1000.0
        snap.median_latency_sec = latencies_ms[len(latencies_ms) // 2] / 1000.0
        p95_idx = min(int(len(latencies_ms) * 0.95), len(latencies_ms) - 1)
        snap.p95_latency_sec = latencies_ms[p95_idx] / 1000.0

    def _fill_tokens(self, snap: PEISnapshot, spans: list[dict[str, Any]]) -> None:
        """Primary usage metric: actual tokens from API responses."""
        budget_path = self._store / "budget.json"

        if spans:
            snap.total_input_tokens = sum(int(s.get("input_tokens") or 0) for s in spans)
            snap.total_output_tokens = sum(int(s.get("output_tokens") or 0) for s in spans)
            by_model: dict[str, int] = {}
            for s in spans:
                model = s.get("model_chosen") or "unknown"
                tok = int(s.get("input_tokens") or 0) + int(s.get("output_tokens") or 0)
                by_model[model] = by_model.get(model, 0) + tok
            snap.tokens_by_model = dict(sorted(by_model.items(), key=lambda x: -x[1]))

        # Prefer budget ledger when spans lack token data (legacy runs)
        if budget_path.exists() and snap.total_tokens == 0:
            try:
                records = json.loads(budget_path.read_text(encoding="utf-8"))
                if isinstance(records, list):
                    api_records = [r for r in records if r.get("request_type") != "cache_hit"]
                    snap.total_input_tokens = sum(int(r.get("input_tokens") or 0) for r in api_records)
                    snap.total_output_tokens = sum(int(r.get("output_tokens") or 0) for r in api_records)
                    by_model = {}
                    for r in api_records:
                        model = r.get("model") or "unknown"
                        tok = int(r.get("input_tokens") or 0) + int(r.get("output_tokens") or 0)
                        by_model[model] = by_model.get(model, 0) + tok
                    snap.tokens_by_model = dict(sorted(by_model.items(), key=lambda x: -x[1]))
            except (json.JSONDecodeError, OSError):
                pass

        snap.total_tokens = snap.total_input_tokens + snap.total_output_tokens
        n = snap.total_tasks or max(len(spans), 1)
        snap.avg_tokens_per_task = _safe_div(snap.total_tokens, n)

    def _fill_cost(self, snap: PEISnapshot, spans: list[dict[str, Any]]) -> None:
        if spans:
            costs = [float(s.get("cost_usd") or 0) for s in spans]
            snap.total_cost_usd = sum(costs)
            snap.avg_cost_per_task_usd = snap.total_cost_usd / len(spans)

        budget_path = self._store / "budget.json"
        if budget_path.exists():
            try:
                records = json.loads(budget_path.read_text(encoding="utf-8"))
                if isinstance(records, list):
                    api_records = [r for r in records if r.get("request_type") != "cache_hit"]
                    cache_records = [r for r in records if r.get("request_type") == "cache_hit"]
                    snap.cache_hits = len(cache_records)
                    snap.cache_savings_usd = sum(
                        float(r.get("saved_cost") or 0) for r in cache_records
                    )
                    ledger_cost = sum(float(r.get("cost") or 0) for r in api_records)
                    if ledger_cost > 0:
                        snap.total_cost_usd = ledger_cost
                        snap.avg_cost_per_task_usd = _safe_div(ledger_cost, len(api_records))
            except (json.JSONDecodeError, OSError):
                pass

        snap.budget_used_pct = _safe_div(snap.total_cost_usd, self._monthly_budget) * 100

        n = max(snap.total_tasks, 1)
        snap.sonnet_baseline_cost_usd = n * _BASELINE_SONNET_COST
        snap.copilot_baseline_cost_usd = n * _BASELINE_COPILOT_COST
        if snap.sonnet_baseline_cost_usd > 0:
            snap.savings_vs_sonnet_pct = (
                (snap.sonnet_baseline_cost_usd - snap.total_cost_usd)
                / snap.sonnet_baseline_cost_usd
                * 100
            )
        if snap.copilot_baseline_cost_usd > 0:
            snap.savings_vs_copilot_pct = (
                (snap.copilot_baseline_cost_usd - snap.total_cost_usd)
                / snap.copilot_baseline_cost_usd
                * 100
            )

    def _fill_model_mix(self, snap: PEISnapshot, spans: list[dict[str, Any]]) -> None:
        mix: dict[str, int] = {}
        cheap = 0
        for s in spans:
            model = s.get("model_chosen") or "unknown"
            mix[model] = mix.get(model, 0) + 1
            if model in self._CHEAP_MODELS or any(c in model for c in ("DeepSeek", "Gemini", "Flash")):
                cheap += 1
        snap.model_mix = dict(sorted(mix.items(), key=lambda x: -x[1]))
        snap.cheap_tier_pct = _safe_div(cheap, len(spans)) * 100 if spans else 0.0

    def _fill_learning(self, snap: PEISnapshot) -> None:
        try:
            from self_learning_metrics import SelfLearningMetrics

            sl = SelfLearningMetrics(store_path=str(self._store)).snapshot()
            snap.error_patterns = sl.error_patterns_recorded
            snap.tools_synthesized = sl.tools_synthesized
            snap.prompt_evolved = sl.guidelines_version > 0
        except Exception:
            patterns_path = self._store / "error_patterns.jsonl"
            if patterns_path.exists():
                snap.error_patterns = sum(
                    1 for line in patterns_path.read_text().splitlines() if line.strip()
                )

        skills_path = self._store / "skills" / "index.json"
        if skills_path.exists():
            try:
                idx = json.loads(skills_path.read_text(encoding="utf-8"))
                snap.skills_count = len(idx) if isinstance(idx, dict) else 0
            except json.JSONDecodeError:
                pass

        snap.learning_sessions = snap.error_patterns + snap.skills_count

    def _fill_pei(self, snap: PEISnapshot) -> None:
        """
        PEI = (Quality × Speed) / Tokens

        Quality: task success rate (0–1)
        Speed:   inverse avg latency in seconds (capped)
        Tokens:  avg tokens per task (primary efficiency measure)
        """
        quality = max(snap.task_success_rate, 0.01)
        speed = 1.0 / max(snap.avg_latency_sec, 0.1) if snap.avg_latency_sec else 1.0
        speed = min(speed, 10.0)  # cap so outliers don't dominate
        tokens = max(snap.avg_tokens_per_task, 1.0)
        snap.pei_score = (quality * speed * 1000.0) / tokens

    # ── Output ────────────────────────────────────────────────────────────

    def print_summary(self) -> None:
        s = self.snapshot()
        w = 62
        print("╭" + "─" * w + "╮")
        print(f"│  AWOS Project Efficiency Report{' ' * (w - 34)}│")
        print(f"│  {s.project_name[:w - 6]:<{w - 6}}│")
        print(f"│  {s.captured_at}{' ' * (w - len(s.captured_at) - 2)}│")
        print("├" + "─" * w + "┤")
        print(f"│  PEI SCORE: {s.pei_score:,.0f}{' ' * (w - 15 - len(f'{s.pei_score:,.0f}'))}│")
        print("├" + "─" * w + "┤")
        print(f"│  QUALITY{' ' * (w - 10)}│")
        print(f"│    Task success rate     {s.task_success_rate * 100:5.1f}%{' ' * (w - 30)}│")
        print(f"│    Goals complete        {s.goals_complete}/{s.goals_total}{' ' * (w - 28 - len(str(s.goals_complete)) - len(str(s.goals_total)))}│")
        print("├" + "─" * w + "┤")
        print(f"│  SPEED{' ' * (w - 8)}│")
        print(f"│    Avg latency           {s.avg_latency_sec:5.2f}s{' ' * (w - 30)}│")
        print(f"│    P95 latency           {s.p95_latency_sec:5.2f}s{' ' * (w - 30)}│")
        print("├" + "─" * w + "┤")
        print(f"│  TOKENS (from API responses){' ' * (w - 30)}│")
        print(f"│    Total tokens          {s.total_tokens:>10,}{' ' * (w - 30 - len(f'{s.total_tokens:,}'))}│")
        print(f"│    Input / Output        {s.total_input_tokens:,} / {s.total_output_tokens:,}{' ' * max(0, w - 30 - len(f'{s.total_input_tokens:,} / {s.total_output_tokens:,}'))}│")
        print(f"│    Avg per task          {s.avg_tokens_per_task:>10,.0f}{' ' * (w - 30 - len(f'{s.avg_tokens_per_task:,.0f}'))}│")
        print("├" + "─" * w + "┤")
        print(f"│  COST (derived from tokens){' ' * (w - 29)}│")
        print(f"│    Total spend           ${s.total_cost_usd:.4f}{' ' * (w - 30 - len(f'{s.total_cost_usd:.4f}'))}│")
        print(f"│    Avg per task          ${s.avg_cost_per_task_usd:.6f}{' ' * (w - 30 - len(f'{s.avg_cost_per_task_usd:.6f}'))}│")
        print(f"│    Cache savings         ${s.cache_savings_usd:.2f}{' ' * (w - 29 - len(f'{s.cache_savings_usd:.2f}'))}│")
        print("├" + "─" * w + "┤")
        print(f"│  VS NAIVE PREMIUM ROUTING{' ' * (w - 27)}│")
        print(f"│    Always-Sonnet would   ${s.sonnet_baseline_cost_usd:.2f}{' ' * (w - 29 - len(f'{s.sonnet_baseline_cost_usd:.2f}'))}│")
        print(f"│    Savings               {s.savings_vs_sonnet_pct:5.1f}%{' ' * (w - 28)}│")
        print(f"│    Cheap-tier routing    {s.cheap_tier_pct:5.1f}% of tasks{' ' * (w - 36)}│")
        print("├" + "─" * w + "┤")
        print(f"│  LEARNING STATE{' ' * (w - 17)}│")
        print(f"│    Error patterns        {s.error_patterns}{' ' * (w - 28 - len(str(s.error_patterns)))}│")
        print(f"│    Skills indexed        {s.skills_count}{' ' * (w - 28 - len(str(s.skills_count)))}│")
        print(f"│    Tools synthesized     {s.tools_synthesized}{' ' * (w - 28 - len(str(s.tools_synthesized)))}│")
        evolved = "yes" if s.prompt_evolved else "not yet"
        print(f"│    Prompt evolved        {evolved}{' ' * (w - 28 - len(evolved))}│")
        print("╰" + "─" * w + "╯")

    def write_html(self, output_path: str | Path) -> Path:
        s = self.snapshot()
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self._render_html(s), encoding="utf-8")
        return out

    def _render_html(self, s: PEISnapshot) -> str:
        model_rows = "".join(
            f"<tr><td>{model}</td><td>{count}</td>"
            f"<td>{count / max(s.total_tasks, 1) * 100:.1f}%</td></tr>"
            for model, count in s.model_mix.items()
        )
        savings_color = "#22c55e" if s.savings_vs_sonnet_pct > 0 else "#ef4444"
        quality_pct = s.task_success_rate * 100

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AWOS PEI Report — {s.project_name}</title>
<style>
  :root {{ --bg:#0f172a; --card:#1e293b; --text:#e2e8f0; --muted:#94a3b8;
           --accent:#38bdf8; --green:#22c55e; --amber:#f59e0b; }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ font-family: system-ui, -apple-system, sans-serif; background:var(--bg);
          color:var(--text); line-height:1.6; padding:2rem; }}
  .container {{ max-width:900px; margin:0 auto; }}
  h1 {{ font-size:1.75rem; margin-bottom:0.25rem; }}
  .subtitle {{ color:var(--muted); margin-bottom:2rem; }}
  .pei-hero {{ background:linear-gradient(135deg,#1e3a5f,#1e293b);
               border:1px solid #334155; border-radius:12px; padding:2rem;
               text-align:center; margin-bottom:2rem; }}
  .pei-score {{ font-size:3.5rem; font-weight:700; color:var(--accent); }}
  .pei-label {{ color:var(--muted); font-size:0.9rem; margin-top:0.5rem; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(250px,1fr)); gap:1rem; margin-bottom:2rem; }}
  .card {{ background:var(--card); border:1px solid #334155; border-radius:8px; padding:1.25rem; }}
  .card h2 {{ font-size:0.75rem; text-transform:uppercase; letter-spacing:0.05em;
              color:var(--muted); margin-bottom:0.75rem; }}
  .metric {{ font-size:1.75rem; font-weight:600; }}
  .metric-sub {{ color:var(--muted); font-size:0.85rem; margin-top:0.25rem; }}
  table {{ width:100%; border-collapse:collapse; margin-top:0.75rem; }}
  th, td {{ text-align:left; padding:0.5rem 0.75rem; border-bottom:1px solid #334155; }}
  th {{ color:var(--muted); font-size:0.75rem; text-transform:uppercase; }}
  .savings {{ color:{savings_color}; font-weight:600; }}
  .footer {{ color:var(--muted); font-size:0.8rem; margin-top:2rem; text-align:center; }}
  .bar {{ height:8px; background:#334155; border-radius:4px; margin-top:0.5rem; overflow:hidden; }}
  .bar-fill {{ height:100%; background:var(--accent); border-radius:4px; }}
</style>
</head>
<body>
<div class="container">
  <h1>AWOS Project Efficiency Report</h1>
  <p class="subtitle">{s.project_name} · Generated {s.captured_at[:10]}</p>

  <div class="pei-hero">
    <div class="pei-score">{s.pei_score:,.0f}</div>
    <div class="pei-label">Project Efficiency Index (PEI) = Quality × Speed ÷ Tokens</div>
  </div>

  <div class="grid">
    <div class="card">
      <h2>Quality</h2>
      <div class="metric">{quality_pct:.1f}%</div>
      <div class="metric-sub">Task success rate · {s.goals_complete}/{s.goals_total} goals complete</div>
      <div class="bar"><div class="bar-fill" style="width:{min(quality_pct,100):.0f}%"></div></div>
    </div>
    <div class="card">
      <h2>Speed</h2>
      <div class="metric">{s.avg_latency_sec:.2f}s</div>
      <div class="metric-sub">Avg latency · P95 {s.p95_latency_sec:.2f}s</div>
    </div>
    <div class="card">
      <h2>Tokens</h2>
      <div class="metric">{s.total_tokens:,}</div>
      <div class="metric-sub">{s.total_input_tokens:,} in · {s.total_output_tokens:,} out · {s.avg_tokens_per_task:,.0f}/task</div>
    </div>
    <div class="card">
      <h2>Cost (derived)</h2>
      <div class="metric">${s.total_cost_usd:.4f}</div>
      <div class="metric-sub">${s.avg_cost_per_task_usd:.6f}/task · from token counts</div>
    </div>
  </div>

  <div class="card" style="margin-bottom:1rem;">
    <h2>Cost Comparison — Why AWOS Routing Matters</h2>
    <table>
      <tr><th>Routing strategy</th><th>Total cost</th><th>vs AWOS</th></tr>
      <tr><td><strong>AWOS (learned routing)</strong></td><td>${s.total_cost_usd:.2f}</td><td>—</td></tr>
      <tr><td>Always Claude Sonnet</td><td>${s.sonnet_baseline_cost_usd:.2f}</td>
          <td class="savings">+{s.savings_vs_sonnet_pct:.0f}% more expensive</td></tr>
      <tr><td>Always Copilot-equivalent</td><td>${s.copilot_baseline_cost_usd:.2f}</td>
          <td class="savings">+{s.savings_vs_copilot_pct:.0f}% more expensive</td></tr>
    </table>
    <p style="margin-top:1rem;color:var(--muted);font-size:0.85rem;">
      {s.cheap_tier_pct:.0f}% of tasks routed to cheap tiers (DeepSeek/Gemini).
      AWOS escalates to premium models only on evidence — not by default.
    </p>
  </div>

  <div class="card" style="margin-bottom:1rem;">
    <h2>Model Mix</h2>
    <table>
      <tr><th>Model</th><th>Tasks</th><th>Share</th></tr>
      {model_rows or '<tr><td colspan="3">No task data yet</td></tr>'}
    </table>
  </div>

  <div class="card">
    <h2>Learning State (.awos/)</h2>
    <table>
      <tr><td>Error patterns recorded</td><td>{s.error_patterns}</td></tr>
      <tr><td>Success skills indexed</td><td>{s.skills_count}</td></tr>
      <tr><td>Tools synthesized</td><td>{s.tools_synthesized}</td></tr>
      <tr><td>Prompt evolved</td><td>{'Yes' if s.prompt_evolved else 'Pending (needs 10 sessions)'}</td></tr>
      <tr><td>Cache hits / savings</td><td>{s.cache_hits} hits · ${s.cache_savings_usd:.2f} saved</td></tr>
    </table>
    <p style="margin-top:1rem;color:var(--muted);font-size:0.85rem;">
      The learning state compounds. Week-over-week PEI improvement is the proof
      that AWOS is worth deploying — not feature count.
    </p>
  </div>

  <p class="footer">
    AWOS — Learnable Operating System for Autonomous Work · awos.dev<br>
    This report is generated from live .awos/ runtime data. No estimates.
  </p>
</div>
</body>
</html>"""
