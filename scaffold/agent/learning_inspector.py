"""
learning_inspector.py — P6 Self-Improvement Introspection Layer.

Reads all learned components (LinUCB, GP World Model, StrategyRouter,
PRM, SkillLibrary) and produces human-readable insights about what
AWOS has learned from its own execution history.

This is the "mirror" for the self-learning system — shows the agent
what it knows, how confident it is, and where it should focus next.

Usage:
    inspector = LearningInspector()
    report = inspector.report()
    print(report.render())

    # Or run as CLI:
    python3 -m scaffold.agent.learning_inspector
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── ANSI (reuse pattern from live_renderer) ───────────────────────────────────
RESET = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"
G = "\033[32m"; Y = "\033[33m"; B = "\033[34m"; C = "\033[36m"; R = "\033[31m"; M = "\033[35m"
def _g(t): return f"{G}{t}{RESET}"
def _y(t): return f"{Y}{t}{RESET}"
def _b(t): return f"{B}{t}{RESET}"
def _c(t): return f"{C}{t}{RESET}"
def _r(t): return f"{R}{t}{RESET}"
def _m(t): return f"{M}{t}{RESET}"
def _dim(t): return f"{DIM}{t}{RESET}"
def _bold(t): return f"{BOLD}{t}{RESET}"


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ModelInsight:
    """What LinUCB has learned about one model tier."""
    model_name: str
    updates: int
    top_task_types: List[str]        # task types where this model excels
    learned_weights: Dict[str, float]
    estimated_success_rate: float
    uncertainty: float               # high = still exploring


@dataclass
class StrategyInsight:
    """What StrategyRouter has learned about one strategy."""
    strategy_name: str
    task_type: str
    win_rate: float
    sample_count: int
    ucb_score: float


@dataclass
class LearningVelocity:
    """How fast is the agent improving?"""
    episodes_total: int
    linucb_updates: int
    gp_fitted: bool
    gp_n_models: int
    prm_ready: bool
    prm_accuracy: float              # 0.0 if not measured yet
    skills_count: int
    mcts_traces: int
    improvement_trend: str           # "improving", "stable", "regressing", "insufficient_data"
    confidence: float                # 0-1


@dataclass
class CurriculumSuggestion:
    """Where should AWOS focus learning next?"""
    task_type: str
    reason: str
    suggested_action: str
    priority: str                    # "high" | "medium" | "low"


@dataclass
class ReflexionStats:
    """P8: What has the agent learned from its own failures?"""
    total_critiques: int
    files_with_critiques: int
    most_failing_file: str
    most_common_type: str
    by_failure_type: Dict[str, int]   # WORKER_FAIL / VERIFY_FAIL / TEST_FAIL counts


@dataclass
class LearningReport:
    velocity: LearningVelocity
    model_insights: List[ModelInsight]
    strategy_insights: List[StrategyInsight]
    curriculum: List[CurriculumSuggestion]
    raw_linucb_summary: Dict[str, Any]
    reflexion: Optional["ReflexionStats"] = None

    def render(self) -> str:
        lines = []
        _sep = "─" * 66

        lines.append(f"\n{_b('╔══════════════════════════════════════════════════════════════════╗')}")
        lines.append(f"{_b('║')}{_bold('  AWOS Self-Learning Inspector — What Has the Agent Learned?      ')}{_b('║')}")
        lines.append(f"{_b('╚══════════════════════════════════════════════════════════════════╝')}")

        # ── Learning Velocity ──────────────────────────────────────────────
        v = self.velocity
        lines.append(f"\n{_bold('─── Learning Velocity ───────────────────────────────────────────')}")
        trend_col = G if v.improvement_trend == "improving" else (Y if v.improvement_trend == "stable" else R)
        trend_icon = "↑" if v.improvement_trend == "improving" else ("→" if v.improvement_trend == "stable" else "↓")
        lines.append(f"  Trend:            {trend_col}{trend_icon} {v.improvement_trend}{RESET}  (confidence {v.confidence:.0%})")
        lines.append(f"  Episodes logged:  {_bold(str(v.episodes_total))}")
        lines.append(f"  LinUCB updates:   {v.linucb_updates}  {'(' + _g('routing') + ')' if v.linucb_updates >= 20 else _dim('(warming up, need 20+)')}")
        lines.append(f"  GP World Model:   {'  ' + _g('fitted (' + str(v.gp_n_models) + ' action models)') if v.gp_fitted else '  ' + _dim('waiting for 50+ episodes')}")
        _prm_str = _g("active (acc=" + f"{v.prm_accuracy:.0%}" + ")") if v.prm_ready else _dim("dormant, need 500+ MCTS traces")
        lines.append(f"  PRM oracle:         {_prm_str}")
        lines.append(f"  Skills learned:   {_g(str(v.skills_count)) if v.skills_count > 0 else _dim('0 (run more tasks)')}")
        lines.append(f"  MCTS traces:      {v.mcts_traces}  {_g('PRM threshold reached!') if v.mcts_traces >= 500 else _dim(f'({500-v.mcts_traces} more needed for PRM)')}")

        # ── What LinUCB Learned ────────────────────────────────────────────
        if self.model_insights:
            lines.append(f"\n{_bold('─── Model Routing Intelligence (LinUCB learned weights) ─────────')}")
            lines.append(f"  {_dim('Feature → weight tells you: does this feature make this model more likely to succeed?')}")
            lines.append(f"  {_dim('Positive = helpful, negative = harmful for that model')}")
            lines.append("")
            for mi in self.model_insights:
                conf = _g("high confidence") if mi.updates >= 30 else (_y("medium") if mi.updates >= 10 else _dim("low confidence"))
                lines.append(f"  {_bold(mi.model_name):<30}  {mi.updates} updates  {conf}")
                if mi.learned_weights:
                    top = sorted(mi.learned_weights.items(), key=lambda x: abs(x[1]), reverse=True)[:4]
                    for feat, weight in top:
                        bar_col = G if weight > 0 else R
                        bar = "█" * int(abs(weight) * 10)
                        lines.append(f"    {feat:<22}  {bar_col}{weight:+.3f} {bar}{RESET}")
                lines.append("")

        # ── StrategyRouter Insights ────────────────────────────────────────
        if self.strategy_insights:
            lines.append(f"{_bold('─── Strategy Win Rates (UCB-1 bandit learned) ───────────────────')}")
            for si in sorted(self.strategy_insights, key=lambda x: -x.win_rate)[:8]:
                col = G if si.win_rate >= 0.7 else (Y if si.win_rate >= 0.4 else R)
                bar = "█" * int(si.win_rate * 20)
                lines.append(
                    f"  {si.task_type:<14} × {si.strategy_name:<16}  "
                    f"{col}{bar:<20}{RESET}  {si.win_rate:.0%}  ({si.sample_count} tasks)"
                )
            lines.append("")

        # ── Self-Curriculum ────────────────────────────────────────────────
        if self.curriculum:
            lines.append(f"{_bold('─── Self-Curriculum — Where to Focus Learning Next ─────────────')}")
            for s in self.curriculum:
                icon = _r("●") if s.priority == "high" else (_y("●") if s.priority == "medium" else _dim("●"))
                lines.append(f"  {icon}  {_bold(s.task_type):<20}  {s.reason}")
                lines.append(f"       {_dim('→')} {s.suggested_action}")
            lines.append("")

        # ── P8: Reflexion Memory ─────────────────────────────────────
        if self.reflexion is not None:
            r = self.reflexion
            lines.append(f"{_bold('─── Reflexion Memory (Learning from Failure) ───────────────')}")
            crit_col = _g if r.total_critiques >= 10 else (_y if r.total_critiques >= 1 else _dim)
            lines.append(f"  Critiques stored:     {crit_col(str(r.total_critiques))}  across {r.files_with_critiques} file(s)")
            if r.most_failing_file and r.most_failing_file != 'none':
                lines.append(f"  Most failing file:    {_y(r.most_failing_file)}")
            if r.most_common_type and r.most_common_type != 'none':
                lines.append(f"  Most common failure:  {_r(r.most_common_type)}")
            if r.by_failure_type:
                for ftype, count in sorted(r.by_failure_type.items(), key=lambda x: -x[1]):
                    col = _r if 'TEST' in ftype else (_y if 'VERIFY' in ftype else _dim)
                    lines.append(f"    {col(ftype):<22}  {count} critique(s)")
            if r.total_critiques == 0:
                lines.append(f"  {_dim('No failures critiqued yet — critiques accumulate as tasks fail')}")
            lines.append("")

        lines.append(f"{_dim(_sep)}\n")
        return "\n".join(lines)


# ── Inspector ─────────────────────────────────────────────────────────────────

class LearningInspector:
    """
    Reads all AWOS learned models and produces a LearningReport.

    Non-invasive: reads from persisted files only, never modifies state.
    Safe to call at any time — fails gracefully if models aren't trained yet.
    """

    FEATURE_NAMES = [
        "action_length", "is_bug_fix", "is_refactor", "is_new_feature",
        "is_architecture", "has_tests", "complexity", "file_is_core",
        "failure_count", "bias",
    ]

    def __init__(self, awos_dir: str = ".awos") -> None:
        self._dir = Path(awos_dir)

    # ── Public ────────────────────────────────────────────────────────────────

    def report(self) -> LearningReport:
        velocity = self._learning_velocity()
        model_insights = self._model_insights()
        strategy_insights = self._strategy_insights()
        curriculum = self._curriculum(velocity, strategy_insights)
        raw = self._raw_linucb()
        reflexion = self._reflexion_stats()
        return LearningReport(
            velocity=velocity,
            model_insights=model_insights,
            strategy_insights=strategy_insights,
            curriculum=curriculum,
            raw_linucb_summary=raw,
            reflexion=reflexion,
        )

    def to_json(self) -> Dict[str, Any]:
        r = self.report()
        return {
            "velocity": {
                "episodes_total": r.velocity.episodes_total,
                "linucb_updates": r.velocity.linucb_updates,
                "gp_fitted": r.velocity.gp_fitted,
                "prm_ready": r.velocity.prm_ready,
                "skills_count": r.velocity.skills_count,
                "mcts_traces": r.velocity.mcts_traces,
                "improvement_trend": r.velocity.improvement_trend,
                "confidence": round(r.velocity.confidence, 3),
            },
            "model_insights": [
                {
                    "model": m.model_name,
                    "updates": m.updates,
                    "top_task_types": m.top_task_types,
                    "estimated_success_rate": round(m.estimated_success_rate, 3),
                }
                for m in r.model_insights
            ],
            "curriculum": [
                {"task_type": s.task_type, "priority": s.priority, "action": s.suggested_action}
                for s in r.curriculum
            ],
        }

    # ── Private: Learning Velocity ────────────────────────────────────────────

    def _learning_velocity(self) -> LearningVelocity:
        episodes = self._count_reward_episodes()
        linucb_updates, _ = self._load_linucb_state()
        gp_fitted, gp_n = self._load_gp_state()
        prm_ready, prm_acc = self._load_prm_state()
        skills = self._count_skills()
        mcts = self._count_mcts_traces()
        trend, conf = self._compute_trend(episodes, linucb_updates, gp_fitted)
        return LearningVelocity(
            episodes_total=episodes,
            linucb_updates=linucb_updates,
            gp_fitted=gp_fitted,
            gp_n_models=gp_n,
            prm_ready=prm_ready,
            prm_accuracy=prm_acc,
            skills_count=skills,
            mcts_traces=mcts,
            improvement_trend=trend,
            confidence=conf,
        )

    def _compute_trend(self, episodes: int, linucb_updates: int, gp_fitted: bool) -> Tuple[str, float]:
        if episodes < 10:
            return "insufficient_data", 0.1
        recent = self._recent_success_rate(window=20)
        older = self._recent_success_rate(window=50, skip=20)
        if recent is None or older is None:
            return "stable", 0.3
        delta = recent - older
        if delta > 0.08:
            return "improving", min(0.5 + abs(delta), 0.95)
        elif delta < -0.08:
            return "regressing", min(0.5 + abs(delta), 0.95)
        else:
            return "stable", 0.6

    def _recent_success_rate(self, window: int, skip: int = 0) -> Optional[float]:
        """Read success rate from last N spans (skipping the most recent `skip`)."""
        spans_path = self._dir / "spans.jsonl"
        if not spans_path.exists():
            return None
        lines = spans_path.read_text(errors="ignore").splitlines()
        if skip:
            lines = lines[:-skip] if len(lines) > skip else []
        lines = lines[-window:]
        if not lines:
            return None
        successes = sum(1 for ln in lines if '"success": true' in ln or '"success":true' in ln)
        return successes / len(lines)

    # ── Private: LinUCB ───────────────────────────────────────────────────────

    def _load_linucb_state(self) -> Tuple[int, Any]:
        """Returns (total_updates, weights_payload)."""
        path = self._dir / "linucb_weights.pkl"
        if not path.exists():
            return 0, None
        try:
            import pickle
            with path.open("rb") as f:
                p = pickle.load(f)
            return p.get("total_updates", 0), p
        except Exception:
            return 0, None

    def _raw_linucb(self) -> Dict[str, Any]:
        try:
            from .ml_router import build_ml_router
            router = build_ml_router(weights_path=self._dir / "linucb_weights.pkl")
            return router.summary()
        except Exception:
            return {}

    def _model_insights(self) -> List[ModelInsight]:
        ACTION_NAMES = ["gemini_flash", "deepseek", "haiku", "sonnet"]
        _, payload = self._load_linucb_state()
        if payload is None:
            return []

        try:
            import numpy as np
            A_list = payload.get("A", [])
            b_list = payload.get("b", [])
            insights = []
            for i, (A_raw, b_raw) in enumerate(zip(A_list, b_list)):
                A = np.array(A_raw); b = np.array(b_raw)
                theta = np.linalg.inv(A) @ b
                updates = int(np.sum(np.diag(A)) - len(A))
                weights = {f: float(w) for f, w in zip(self.FEATURE_NAMES, theta)}
                # Estimate success rate from bias term (approximately θ_bias)
                est_rate = max(0.0, min(1.0, float(theta[-1]) + 0.5))
                uncertainty = float(1.0 / (1.0 + updates / 10.0))
                insights.append(ModelInsight(
                    model_name=ACTION_NAMES[i] if i < len(ACTION_NAMES) else f"action_{i}",
                    updates=max(0, updates),
                    top_task_types=self._top_task_types_for(i),
                    learned_weights=weights,
                    estimated_success_rate=est_rate,
                    uncertainty=uncertainty,
                ))
            return insights
        except Exception as e:
            logger.debug("[inspector] model_insights failed: %s", e)
            return []

    def _top_task_types_for(self, action_id: int) -> List[str]:
        """Read reward store episodes and find task types where this model scored best."""
        try:
            import json
            ep_path = self._dir / "episodes.jsonl"
            if not ep_path.exists():
                return []
            from collections import defaultdict
            totals: Dict[str, List[float]] = defaultdict(list)
            for line in ep_path.read_text(errors="ignore").splitlines()[-200:]:
                d = json.loads(line)
                if d.get("action_id") == action_id:
                    t = d.get("task_type", d.get("model_name", "unknown"))
                    totals[t].append(float(d.get("reward", 0)))
            ranked = sorted(totals.items(), key=lambda x: sum(x[1]) / len(x[1]), reverse=True)
            return [k for k, _ in ranked[:3]]
        except Exception:
            return []

    # ── Private: GP ───────────────────────────────────────────────────────────

    def _load_gp_state(self) -> Tuple[bool, int]:
        """Returns (is_fitted, n_action_models)."""
        path = self._dir / "gp_model.pkl"
        if not path.exists():
            return False, 0
        try:
            import pickle
            with path.open("rb") as f:
                gp = pickle.load(f)
            # GPWorldModel stores fitted models in _models dict
            models = getattr(gp, "_models", {})
            return bool(models), len(models)
        except Exception:
            return False, 0

    # ── Private: PRM ─────────────────────────────────────────────────────────

    def _load_prm_state(self) -> Tuple[bool, float]:
        """Returns (ready, estimated_accuracy)."""
        path = self._dir / "prm_weights.pkl"
        if not path.exists():
            return False, 0.0
        # PRM is ready if weights exist; accuracy requires validation data
        try:
            import pickle
            with path.open("rb") as f:
                payload = pickle.load(f)
            acc = float(payload.get("val_accuracy", 0.0))
            return True, acc
        except Exception:
            return True, 0.0

    # ── Private: StrategyRouter ───────────────────────────────────────────────

    def _strategy_insights(self) -> List[StrategyInsight]:
        try:
            from .strategy_config import StrategyRouter
        except ImportError:
            try:
                from strategy_config import StrategyRouter
            except ImportError:
                return []
        try:
            router = StrategyRouter()
            insights = []
            for (task_type, strategy_name), counts in router._counts.items():
                n = counts.get("n", 0)
                w = counts.get("wins", 0)
                if n == 0:
                    continue
                win_rate = w / n
                t_total = router._total.get(task_type, 1)
                ucb = win_rate + math.sqrt(2 * math.log(max(t_total, 1)) / n) if n > 0 else float("inf")
                insights.append(StrategyInsight(
                    strategy_name=strategy_name,
                    task_type=task_type,
                    win_rate=win_rate,
                    sample_count=n,
                    ucb_score=min(ucb, 9.99),
                ))
            return sorted(insights, key=lambda x: -x.win_rate)
        except Exception as e:
            logger.debug("[inspector] strategy_insights failed: %s", e)
            return []

    # ── Private: Self-Curriculum ──────────────────────────────────────────────

    def _curriculum(
        self,
        v: LearningVelocity,
        strategies: List[StrategyInsight],
    ) -> List[CurriculumSuggestion]:
        suggestions = []

        if v.linucb_updates < 20:
            suggestions.append(CurriculumSuggestion(
                task_type="all",
                reason=f"LinUCB needs {20 - v.linucb_updates} more updates before routing policy activates",
                suggested_action="Run diverse tasks (bug fixes, new features, refactors) to warm up the bandit",
                priority="high",
            ))

        if not v.gp_fitted:
            needed = max(0, 50 - v.episodes_total)
            if needed > 0:
                suggestions.append(CurriculumSuggestion(
                    task_type="all",
                    reason=f"GP World Model needs {needed} more episodes for Thompson Sampling",
                    suggested_action="Continue running tasks — GP activates at 50 episodes automatically",
                    priority="high" if needed > 30 else "medium",
                ))

        if not v.prm_ready and v.mcts_traces < 500:
            suggestions.append(CurriculumSuggestion(
                task_type="complex",
                reason=f"PRM needs {500 - v.mcts_traces} more MCTS traces to train",
                suggested_action="Enable AWOS_MCTS=1 and run hard bug-fix tasks to generate traces",
                priority="medium",
            ))

        if v.skills_count < 5:
            suggestions.append(CurriculumSuggestion(
                task_type="repeated_patterns",
                reason="Skill library is sparse — agent can't reuse patterns yet",
                suggested_action="Run similar tasks repeatedly to trigger skill extraction (score > 0.9)",
                priority="medium",
            ))

        # Find weak strategy-task combos that need more exploration
        weak = [s for s in strategies if s.win_rate < 0.4 and s.sample_count >= 5]
        for w in weak[:2]:
            suggestions.append(CurriculumSuggestion(
                task_type=w.task_type,
                reason=f"{w.strategy_name} wins only {w.win_rate:.0%} on {w.task_type} tasks",
                suggested_action=f"Run more {w.task_type} tasks — StrategyRouter will explore alternatives",
                priority="low",
            ))

        if v.improvement_trend == "regressing":
            suggestions.append(CurriculumSuggestion(
                task_type="all",
                reason="Success rate is declining — possible task distribution shift",
                suggested_action="Check recent error patterns, consider resetting escalation history",
                priority="high",
            ))

        return suggestions[:5]

    # ── Private: Misc ─────────────────────────────────────────────────────────

    def _count_reward_episodes(self) -> int:
        for name in ("reward_store.jsonl", "episodes.jsonl"):
            p = self._dir / name
            if p.exists():
                return sum(1 for _ in p.open(errors="ignore"))
        return 0

    def _count_skills(self) -> int:
        d = self._dir / "skills"
        if not d.exists():
            return 0
        return len(list(d.glob("*.md")))

    def _count_mcts_traces(self) -> int:
        p = self._dir / "mcts_traces.jsonl"
        if not p.exists():
            return 0
        return sum(1 for _ in p.open(errors="ignore"))

    # ── Private: Reflexion Memory (P8) ──────────────────────────────────

    def _reflexion_stats(self) -> Optional["ReflexionStats"]:
        """Read ErrorPatternStore summary from disk. Returns None if unavailable."""
        try:
            from .error_pattern_store import ErrorPatternStore
        except ImportError:
            try:
                from error_pattern_store import ErrorPatternStore
            except ImportError:
                return None
        try:
            store = ErrorPatternStore(store_path=str(self._dir / "error_patterns.jsonl"))
            s = store.summary()
            return ReflexionStats(
                total_critiques=s.get("total", 0),
                files_with_critiques=len(s.get("by_file", {})),
                most_failing_file=max(s.get("by_file", {"none": 0}),
                                       key=s.get("by_file", {"none": 0}).get,
                                       default="none"),
                most_common_type=max(s.get("by_type", {"none": 0}),
                                      key=s.get("by_type", {"none": 0}).get,
                                      default="none"),
                by_failure_type=s.get("by_type", {}),
            )
        except Exception as exc:
            logger.debug("[inspector] reflexion_stats failed: %s", exc)
            return ReflexionStats(
                total_critiques=0,
                files_with_critiques=0,
                most_failing_file="none",
                most_common_type="none",
                by_failure_type={},
            )


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="AWOS Learning Inspector")
    parser.add_argument("--dir", default=".awos", help="AWOS data directory")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    inspector = LearningInspector(awos_dir=args.dir)
    if args.json:
        import json
        print(json.dumps(inspector.to_json(), indent=2))
    else:
        print(inspector.report().render())


if __name__ == "__main__":
    main()
