"""
EscalationEngine: 6-tier model ladder with complexity, budget, and failure gates.

Philosophy:
  Start cheap. Escalate on evidence.
  A small model with great context beats a large model with poor context.
  Opus is BANNED — too expensive at any scale.
  Sonnet is restricted — ~10% of requests max, complexity 8+ or 3+ failures.

Ladder (cheapest → most powerful):
  L0  Gemini 2.5 Flash-Lite      $0.001/req  — routing, simple Q&A
  L1  DeepSeek V4 Flash           $0.001/req  — default worker
  L2  OpenRouter                 $0.002/req  — 200+ model catalog (one key)
  L3  GPT-4o-mini (OpenAI)        $0.003/req  — fallback (uses existing credits)
  L4  Claude Haiku 4.5            $0.017/req  — BLOCKED when AWOS_CHEAP_ONLY=true
  L5  Claude Sonnet 4.6           $0.050/req  — BLOCKED when AWOS_CHEAP_ONLY=true

Cheap-only mode (AWOS_CHEAP_ONLY=true):
  Premium tiers (Haiku, Sonnet) are halted. Intelligence comes from context, skills, and
  rotating among Gemini / DeepSeek / OpenRouter / GPT-4o-mini on retry — not escalation.
  OpenRouter (L5) is always available — it is a cheap provider with diverse model access.
"""

import os
from dataclasses import dataclass
from enum import Enum
from typing import Optional

try:
    from .reward_store import ReplayGate
except ImportError:
    from reward_store import ReplayGate



class EscalationLevel(Enum):
    GEMINI_FLASH  = 0   # Router / simple answers
    DEEPSEEK      = 1   # Default code worker
    OPENAI        = 2   # GPT-4o-mini fallback (uses existing OpenAI credits)
    HAIKU         = 3   # Retry tier (Anthropic Claude Haiku)
    SONNET        = 4   # Severely restricted (top tier, Opus banned)
    OPENROUTER    = 5   # Unified access to 200+ models (one API key)
    OPENCODE      = 6   # OpenCode Go — primary coding model


@dataclass
class ModelSpec:
    """Spec for one level of the escalation ladder."""
    level: EscalationLevel
    name: str                   # Human-readable
    model_id: str               # API identifier
    provider: str               # "anthropic" | "deepseek" | "google" | "openrouter"
    cost_per_req: float         # Estimated $USD per average request
    input_price:  float         # $/MTok input
    output_price: float         # $/MTok output
    min_complexity: int         # Task complexity score (1-10) to default here
    min_budget_remaining: float # $ budget required to allow this level
    min_failures: int           # Failure count needed before auto-escalation here


LADDER: list[ModelSpec] = [
    ModelSpec(
        level=EscalationLevel.GEMINI_FLASH,
        name="Gemini 2.5 Flash-Lite",
        model_id="gemini-2.0-flash",
        provider="google",
        cost_per_req=0.001,
        input_price=0.10,
        output_price=0.40,
        min_complexity=0,
        min_budget_remaining=0.0,
        min_failures=0,
    ),
    ModelSpec(
        level=EscalationLevel.DEEPSEEK,
        name="DeepSeek V4 Flash",
        model_id="deepseek-v4-flash",  # OpenCode Go model ID
        provider="deepseek",
        cost_per_req=0.001,
        input_price=0.14,
        output_price=0.28,
        min_complexity=0,
        min_budget_remaining=0.0,
        min_failures=0,
    ),
    ModelSpec(
        level=EscalationLevel.OPENROUTER,
        name="DeepSeek V4 Pro",
        model_id="deepseek-v4-pro",  # OpenCode Go model ID
        provider="deepseek",
        cost_per_req=0.002,
        input_price=0.28,
        output_price=0.56,
        min_complexity=10,  # Only when flash fails
        min_budget_remaining=0.0,
        min_failures=1,  # Use after 1 flash failure
    ),
    ModelSpec(
        level=EscalationLevel.OPENAI,
        name="GPT-4o-mini",
        model_id="gpt-4o-mini",
        provider="openai",
        cost_per_req=0.003,
        input_price=0.15,
        output_price=0.60,
        min_complexity=10,  # Only as fallback after DeepSeek fails
        min_budget_remaining=0.0,
        min_failures=2,  # Use after 2 DeepSeek failures
    ),
    ModelSpec(
        level=EscalationLevel.OPENCODE,
        name="OpenCode Go",
        model_id="deepseek-chat",
        provider="opencode",  # Uses OPENCODE_GO_API_KEY (endpoint TBD)
        cost_per_req=0.001,
        input_price=0.14,
        output_price=0.28,
        min_complexity=99,  # Disabled until endpoint is known
        min_budget_remaining=0.0,
        min_failures=99,
    ),
    ModelSpec(
        level=EscalationLevel.HAIKU,
        name="Claude Haiku 4.5",
        model_id="claude-haiku-4-5",
        provider="anthropic",
        cost_per_req=0.017,
        input_price=1.00,
        output_price=5.00,
        min_complexity=10,  # Only use after failures, not for complexity
        min_budget_remaining=1.0,
        min_failures=3,
    ),
    ModelSpec(
        level=EscalationLevel.SONNET,
        name="Claude Sonnet 4.6",
        model_id="claude-sonnet-4-6",
        provider="anthropic",
        cost_per_req=0.050,
        input_price=3.00,
        output_price=15.00,
        min_complexity=10,  # Only use after failures, not for complexity
        min_budget_remaining=3.0,
        min_failures=5,  # Only after Haiku also fails
    ),
]

LEVEL_MAP: dict[EscalationLevel, ModelSpec] = {m.level: m for m in LADDER}

CHEAP_ONLY_MAX_LEVEL = EscalationLevel.OPENAI
_CHEAP_ROTATION = [
    EscalationLevel.DEEPSEEK,
    EscalationLevel.OPENROUTER,
    EscalationLevel.OPENAI,
]


def is_cheap_only() -> bool:
    """True when premium models are halted (env flag or zero premium budget)."""
    if os.getenv("AWOS_CHEAP_ONLY", "false").lower() in ("1", "true", "yes"):
        return True
    pb = os.getenv("AWOS_PREMIUM_BUDGET", "")
    return pb == "0"


def max_allowed_level() -> EscalationLevel:
    return CHEAP_ONLY_MAX_LEVEL if is_cheap_only() else EscalationLevel.OPENCODE


def allowed_ladder() -> list[ModelSpec]:
    """Return ladder tiers allowed in current mode.
    
    Always includes OpenRouter (level 5) — it is a cheap provider with wide model
    access and works in both cheap-only and premium modes.
    """
    cap = max_allowed_level().value
    return [s for s in LADDER if s.level.value <= cap or s.level == EscalationLevel.OPENROUTER]


@dataclass
class EscalationDecision:
    spec: ModelSpec
    reason: str
    complexity_score: int
    failure_count: int
    budget_remaining: float
    forced: bool = False

    def __repr__(self) -> str:
        return (f"EscalationDecision(model={self.spec.name}, "
                f"complexity={self.complexity_score}, reason={self.reason[:40]})")


class EscalationEngine:
    """
    Decides which model tier to use for a given task.

    Intelligence amplification principle:
      A well-prompted cheap model beats a poorly-prompted expensive one.
      Escalate only when cheaper tiers have genuinely failed or task demands it.

    ml_router (optional LinUCBRouter):
      When provided and is_ready(), overrides heuristic routing with a
      learned contextual bandit policy.  Falls back to heuristics on cold start
      or when the router is None.  Updated via record_outcome().
    """

    def __init__(self, monthly_budget: float = 20.0, performance_tracker=None, ml_router=None):
        self.monthly_budget = monthly_budget
        self._task_history: dict[str, list] = {}  # task_id → [outcomes]
        self._performance = performance_tracker
        self._ml_router = ml_router          # LinUCBRouter | None
        self._feature_extractor = None
        # Premium budget: separate cap on Anthropic spend (default: no cap = None)
        _pb = os.getenv("AWOS_PREMIUM_BUDGET", "")
        self._premium_budget = float(_pb) if _pb else None
        if ml_router is not None:
            try:
                from scaffold.agent.ml_router import TaskFeatureExtractor
                self._feature_extractor = TaskFeatureExtractor()
            except Exception:
                pass

    # ── Public API ────────────────────────────────────────────────────────────

    def _effective_budget(self, spec: ModelSpec, budget_remaining: float) -> float:
        """Return effective budget for a model, accounting for premium cap."""
        if not self._is_premium_model(spec):
            return budget_remaining
        premium_rem = self._premium_budget_remaining()
        if premium_rem is None:
            return budget_remaining
        return min(budget_remaining, premium_rem)

    def decide(
        self,
        task: dict,
        failure_count: int = 0,
        budget_remaining: Optional[float] = None,
        force_level: Optional[EscalationLevel] = None,
        user_wants_best: bool = False,
        dead_providers: Optional[set] = None,
    ) -> EscalationDecision:
        """
        Choose the right model tier for this task.

        Args:
            task:             Planner task dict (must include "complexity" key)
            failure_count:    How many times this exact task has failed already
            budget_remaining: Remaining $ budget this month
            force_level:      Skip logic and use this level
            user_wants_best:  User explicitly asked for best model
        """
        if budget_remaining is None:
            budget_remaining = self.monthly_budget
        if dead_providers is None:
            dead_providers = set()

        complexity_score = self._score_complexity(task)

        # Hard override (capped in cheap-only mode)
        if force_level is not None:
            spec = LEVEL_MAP[force_level]
            cap = max_allowed_level()
            if spec.level.value > cap.value:
                spec = LEVEL_MAP[cap]
                reason = f"Force blocked above {cap.name} — cheap-only mode"
            else:
                reason = f"Forced to {spec.name}"
            return EscalationDecision(
                spec=spec,
                reason=reason,
                complexity_score=complexity_score,
                failure_count=failure_count,
                budget_remaining=budget_remaining,
                forced=True,
            )

        # User explicitly wants best available (capped in cheap-only mode)
        best_level = max_allowed_level()
        best_spec = LEVEL_MAP[best_level]
        if user_wants_best and self._effective_budget(best_spec, budget_remaining) >= best_spec.min_budget_remaining:
            label = "cheap-only best" if is_cheap_only() else "best available"
            return EscalationDecision(
                spec=best_spec,
                reason=f"User requested {label} ({best_spec.name})",
                complexity_score=complexity_score,
                failure_count=failure_count,
                budget_remaining=budget_remaining,
                forced=True,
            )

        ladder = allowed_ladder()
        worker_ladder = [s for s in ladder if s.level != EscalationLevel.GEMINI_FLASH]

        # ── Performance veto (empirical success matrix) ──────────────────
        # Runs BEFORE LinUCB so strong empirical evidence overrides the bandit.
        # Threshold: >=10 samples AND success_rate < 0.50 on default model.
        _perf_override: Optional[ModelSpec] = None
        if self._performance is not None:
            task_type = self._performance._classify_task_type(task.get("action", ""))
            default_model_names = [spec.name for spec in worker_ladder]
            best_name, best_rate = self._performance.best_model_for(
                task_type=task_type,
                candidates=default_model_names,
                window=50,
                min_count=10,
            )
            if best_name is not None and best_rate is not None:
                default_stats = self._performance.get_stats(
                    model=worker_ladder[0].name if worker_ladder else LADDER[1].name,
                    task_type=task_type,
                    window=50,
                )
                default_rate = default_stats.get("success_rate") or 1.0
                if default_rate < 0.50 and best_rate > default_rate:
                    for spec in ladder:
                        if spec.name == best_name and self._effective_budget(spec, budget_remaining) >= spec.min_budget_remaining:
                            _perf_override = spec
                            break

        # ── LinUCB ML router (learned policy) ────────────────────────────
        if (
            self._ml_router is not None
            and self._feature_extractor is not None
            and self._ml_router.is_ready()
        ):
            features = self._feature_extractor.extract(task, failure_count)
            cap = max_allowed_level().value
            budget_mask = [
                spec.level.value <= cap
                and self._effective_budget(spec, budget_remaining) >= spec.min_budget_remaining
                for spec in LADDER
            ]
            action_id = self._ml_router.select(features, budget_mask=budget_mask)
            ml_spec = LADDER[action_id]
            if ml_spec.level.value > cap:
                ml_spec = LEVEL_MAP[max_allowed_level()]
            # Apply performance veto if empirical data is stronger
            if _perf_override is not None and _perf_override.level.value > ml_spec.level.value:
                return EscalationDecision(
                    spec=_perf_override,
                    reason=f"Performance veto over LinUCB: {_perf_override.name} has higher empirical rate for task_type={task_type}",
                    complexity_score=complexity_score,
                    failure_count=failure_count,
                    budget_remaining=budget_remaining,
                )
            return EscalationDecision(
                spec=ml_spec,
                reason=f"LinUCB learned policy (updates={self._ml_router.total_updates()})",
                complexity_score=complexity_score,
                failure_count=failure_count,
                budget_remaining=budget_remaining,
            )

        # ── Cold-start: performance soft hint ─────────────────────────────
        # When LinUCB has < min_samples, use empirical success matrix as a
        # soft routing signal.  Threshold is lower than the hard veto above.
        if (
            self._performance is not None
            and self._ml_router is not None
            and not self._ml_router.is_ready()
            and _perf_override is None
        ):
            task_type = self._performance._classify_task_type(task.get("action", ""))
            default_model_names = [spec.name for spec in worker_ladder]
            recommended = self._performance.recommend_model(task_type, default_model_names)
            if recommended:
                for spec in ladder:
                    if spec.name == recommended and self._effective_budget(spec, budget_remaining) >= spec.min_budget_remaining:
                        return EscalationDecision(
                            spec=spec,
                            reason=f"Performance hint (cold-start): {spec.name} has highest empirical success for {task_type}",
                            complexity_score=complexity_score,
                            failure_count=failure_count,
                            budget_remaining=budget_remaining,
                        )

        # Cheap-only: rotate among cheap tiers on retry instead of escalating
        if is_cheap_only() and failure_count > 0:
            rotated = self._rotate_cheap(failure_count, dead_providers)
            if rotated is not None:
                return EscalationDecision(
                    spec=rotated,
                    reason=f"Cheap-only rotation after {failure_count} failure(s) — {rotated.name}",
                    complexity_score=complexity_score,
                    failure_count=failure_count,
                    budget_remaining=budget_remaining,
                )

        # Walk down the ladder from most capable, find highest affordable level
        # Default: lowest non-dead tier (usually DeepSeek, but skip if tripped)
        chosen = LEVEL_MAP[EscalationLevel.DEEPSEEK]  # fallback
        reason = "Default DeepSeek worker"
        for _s in worker_ladder:
            if _s.provider not in dead_providers:
                chosen = _s
                reason = f"Default {_s.name} (lowest available)"
                break

        for spec in reversed(worker_ladder):
            if spec.provider in dead_providers:
                continue  # skip tripped provider
            qualifies_complexity = complexity_score >= spec.min_complexity
            qualifies_failures   = failure_count   >= spec.min_failures
            qualifies_budget     = self._effective_budget(spec, budget_remaining) >= spec.min_budget_remaining

            if qualifies_budget and (qualifies_complexity or qualifies_failures):
                chosen = spec
                reasons = []
                if qualifies_complexity:
                    reasons.append(f"complexity={complexity_score}>={spec.min_complexity}")
                if qualifies_failures:
                    reasons.append(f"failures={failure_count}>={spec.min_failures}")
                reason = f"Escalated to {spec.name}: {', '.join(reasons)}"
                break

        # Apply performance veto to heuristic result too
        if _perf_override is not None and _perf_override.level.value > chosen.level.value:
            chosen = _perf_override
            _task_type = task.get("complexity", "unknown")
            reason = f"Performance veto: {_perf_override.name} has higher empirical rate for task_type={_task_type}"

        if is_cheap_only():
            reason = f"{reason} [cheap-only]"

        return EscalationDecision(
            spec=chosen,
            reason=reason,
            complexity_score=complexity_score,
            failure_count=failure_count,
            budget_remaining=budget_remaining,
        )

    def record_outcome(
        self,
        task_id: str,
        level: EscalationLevel,
        success: bool,
        task: Optional[dict] = None,
        reward: Optional[float] = None,
    ):
        """Track outcomes to inform future escalations and update LinUCB weights."""
        if task_id not in self._task_history:
            self._task_history[task_id] = []
        self._task_history[task_id].append({"level": level.value, "success": success})

        # ── Update LinUCB with this outcome (gated by ReplayGate) ─────────
        if (
            self._ml_router is not None
            and self._feature_extractor is not None
            and task is not None
        ):
            failure_count = self.failure_count(task_id)
            features = self._feature_extractor.extract(task, failure_count)
            _reward = reward if reward is not None else (1.0 if success else 0.0)

            # Build a minimal Episode-like object for the gate
            _gate = getattr(self, '_replay_gate', None)
            if _gate is None:
                self._replay_gate = ReplayGate()
                _gate = self._replay_gate

            class _Ep:
                pass
            ep = _Ep()
            ep.reward = _reward
            ep.features = list(features)
            ep.action_id = level.value
            ep.episode_id = task_id

            if _gate.admit(ep):
                self._ml_router.update(features, level.value, _reward)

    def failure_count(self, task_id: str) -> int:
        """Count consecutive failures for a task."""
        history = self._task_history.get(task_id, [])
        count = 0
        for entry in reversed(history):
            if not entry["success"]:
                count += 1
            else:
                break
        return count

    def reset_task(self, task_id: str) -> None:
        """Delete task history entry if it exists."""
        self._task_history.pop(task_id, None)

    # ── Complexity Scoring ────────────────────────────────────────────────────

    def _rotate_cheap(self, failure_count: int, dead_providers: set) -> Optional[ModelSpec]:
        """Rotate among cheap tiers on retry — squeeze intelligence from context, not price."""
        for i in range(len(_CHEAP_ROTATION)):
            level = _CHEAP_ROTATION[(failure_count - 1 + i) % len(_CHEAP_ROTATION)]
            spec = LEVEL_MAP[level]
            if spec.provider not in dead_providers:
                return spec
        return LEVEL_MAP[EscalationLevel.DEEPSEEK]

    def _score_complexity(self, task: dict) -> int:
        """
        Score task complexity 1-10 from the task dict.

        Combines:
          - Planner's complexity label (low/medium/high)
          - Action text signals (new feature, refactor, system, etc.)
          - File type hints (.py core files vs tests/docs)
        """
        base = {"low": 3, "medium": 6, "high": 8}.get(
            task.get("complexity", "medium").lower(), 6
        )

        action = task.get("action", "").lower()
        file_path = task.get("file", "").lower()

        # Boost signals
        boosts = [
            ("from scratch", 2), ("new module", 2), ("new service", 2),
            ("architecture", 2), ("refactor entire", 2), ("authentication", 1),
            ("database", 1), ("pipeline", 1), ("integrate", 1), ("api layer", 1),
        ]
        boost = sum(v for kw, v in boosts if kw in action)

        # Reduce for simple files
        if any(x in file_path for x in ["test_", "_test", "docs/", "readme"]):
            boost -= 1

        return max(1, min(10, base + boost))

    def _premium_budget_remaining(self) -> float | None:
        """Remaining premium budget, or None if no cap is set."""
        if self._premium_budget is None:
            return None
        try:
            from scaffold.agent.budget_ledger import get_ledger
            spent = get_ledger().get_premium_spent()
            return max(0.0, self._premium_budget - spent)
        except Exception:
            return self._premium_budget

    def _is_premium_model(self, spec: ModelSpec) -> bool:
        """Check if a model spec is an Anthropic (premium) model."""
        return spec.provider == "anthropic"

    def summary(self, decision: EscalationDecision) -> str:
        """Human-readable decision summary."""
        spec = decision.spec
        return (
            f"[{spec.name}] complexity={decision.complexity_score}/10 "
            f"failures={decision.failure_count} "
            f"budget_left=${decision.budget_remaining:.2f} "
            f"— {decision.reason}"
        )
