"""
Phase 2: Queueing Theory + Regret Tracking

M/M/1 Queue Model
-----------------
Assumes: Poisson task arrivals (memoryless inter-arrival), exponential service
times (memoryless per-task), single server (sequential execution), infinite
queue capacity.

Equations (all derivations inline):
    ρ  = λ/μ                     server utilization (stable iff ρ < 1)
    Lq = ρ²/(1-ρ)                mean tasks waiting in queue    [dimensionless]
    Wq = Lq/λ = ρ/(μ(1-ρ))      mean queue wait time           [seconds]
    W  = Wq + 1/μ                mean system time (wait+service)[seconds]
    L  = λ·W                     Little's Law: mean tasks in system

Where:
    λ  = 1/mean(inter_arrival_times)   [arrivals/sec]
    μ  = 1/mean(service_times)         [completions/sec]
    service_time = worker_latency_ms / 1000

The gap between theoretical Wq and actual measured queue_wait_ms reveals
whether the M/M/1 assumption holds (Poisson arrivals, exponential service).
Large gaps indicate correlated arrivals or high service-time variance (M/G/1
territory).

LinUCB Regret Tracker
---------------------
Pseudo-regret: at each round t, regret_t = oracle_reward_t - actual_reward_t
where oracle_reward_t is the running mean reward of the empirically best action
seen so far.

Cumulative regret grows as O(√(d·T·log T)) for LinUCB (Li et al. 2010),
where d=feature dimension, T=rounds. Tracking this empirically tells you:
  - Is the bandit actually learning? (regret slope decreasing over time)
  - Is it converging faster or slower than theory predicts?

Reference:
    Li, L., Chu, W., Langford, J., & Schapire, R. E. (2010).
    A contextual-bandit approach to personalized news article recommendation.
    WWW 2010.  https://arxiv.org/abs/1003.0146
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .observability import TaskSpan


# ── Constants ─────────────────────────────────────────────────────────────────

_MIN_SAMPLES = 3          # minimum spans needed for meaningful M/M/1 estimate
_SATURATION_WARN = 0.80   # ρ threshold for saturation warning
_N_FEATURES = 10          # LinUCB feature dimension (must match ml_router.py)
_N_ACTIONS = 5            # number of model tiers

ACTION_NAMES = {
    0: "gemini_flash",
    1: "deepseek",
    2: "openai",
    3: "haiku",
    4: "sonnet",
}


# ── M/M/1 Queue ───────────────────────────────────────────────────────────────

@dataclass
class QueueState:
    """
    Snapshot of M/M/1 queue statistics computed from recent TaskSpans.

    All time values are in milliseconds for human readability.
    Rates (λ, μ) are in tasks/second.
    """
    n_samples: int

    # Measured rates
    arrival_rate: float          # λ — tasks/second
    service_rate: float          # μ — tasks/second

    # Derived dimensionless
    utilization: float           # ρ = λ/μ

    # Theoretical M/M/1 predictions (ms)
    theoretical_queue_wait_ms: float    # Wq = ρ/(μ(1-ρ))
    theoretical_system_time_ms: float   # W  = Wq + 1/μ

    # Little's Law quantities
    mean_queue_length: float     # Lq = ρ²/(1-ρ)
    mean_system_length: float    # L  = λ·W

    # Empirical (from spans) — for comparison
    actual_queue_wait_ms: float
    actual_system_time_ms: float

    # Health
    is_stable: bool              # ρ < 1.0

    @property
    def saturation_pct(self) -> float:
        return self.utilization * 100.0

    @property
    def model_vs_reality_gap_ms(self) -> float:
        """
        Difference between theoretical and actual queue wait.
        Large positive gap → arrivals are burstier than Poisson (M/G/1 needed).
        Large negative gap → system is over-provisioned or arrivals are sub-Poisson.
        """
        return self.theoretical_queue_wait_ms - self.actual_queue_wait_ms

    @property
    def saturation_warning(self) -> Optional[str]:
        if not self.is_stable:
            return f"UNSTABLE: ρ={self.utilization:.3f} ≥ 1.0 — queue grows without bound"
        if self.utilization >= _SATURATION_WARN:
            return (
                f"WARNING: ρ={self.utilization:.2f} — system is {self.saturation_pct:.0f}% "
                f"saturated, Wq={self.theoretical_queue_wait_ms:.0f}ms"
            )
        return None

    def display(self) -> str:
        status = "STABLE" if self.is_stable else "UNSTABLE"
        warn = f"\n│  ⚠ {self.saturation_warning}" if self.saturation_warning else ""
        gap_sign = "+" if self.model_vs_reality_gap_ms >= 0 else ""
        return "\n".join([
            f"┌─ M/M/1 QUEUE  (n={self.n_samples}) ──────────────────────────────────",
            f"│  λ={self.arrival_rate:.4f}/s  μ={self.service_rate:.4f}/s  "
            f"ρ={self.utilization:.3f} [{status}]  sat={self.saturation_pct:.1f}%",
            f"│  Theory  Wq={self.theoretical_queue_wait_ms:.1f}ms  "
            f"W={self.theoretical_system_time_ms:.1f}ms  "
            f"Lq={self.mean_queue_length:.3f}  L={self.mean_system_length:.3f}",
            f"│  Actual  Wq={self.actual_queue_wait_ms:.1f}ms  "
            f"W={self.actual_system_time_ms:.1f}ms  "
            f"gap={gap_sign}{self.model_vs_reality_gap_ms:.1f}ms",
            f"└────────────────────────────────────────────────────────────────",
        ]) + warn


class MM1Queue:
    """
    Estimates M/M/1 queue parameters from a list of TaskSpans.

    Usage:
        spans = obs_store.get_recent(100)
        state = MM1Queue.from_spans(spans)
        print(state.display())
    """

    @staticmethod
    def from_spans(spans: List["TaskSpan"]) -> Optional[QueueState]:
        """
        Compute QueueState from a list of spans.
        Returns None if fewer than _MIN_SAMPLES spans are available.

        All spans must have enqueue_ts and complete_ts set (non-zero).
        """
        valid = [s for s in spans if s.enqueue_ts > 0 and s.complete_ts > 0]
        if len(valid) < _MIN_SAMPLES:
            return None

        # Sort by arrival time
        valid.sort(key=lambda s: s.enqueue_ts)

        # ── Estimate λ from inter-arrival times ───────────────────────────
        inter_arrivals = [
            valid[i + 1].enqueue_ts - valid[i].enqueue_ts
            for i in range(len(valid) - 1)
        ]
        # Guard against zero inter-arrival (parallel tasks recorded at same ms)
        inter_arrivals = [t for t in inter_arrivals if t > 0]
        if not inter_arrivals:
            return None

        mean_inter = sum(inter_arrivals) / len(inter_arrivals)
        lam = 1.0 / mean_inter  # λ — arrivals per second

        # ── Estimate μ from service times (worker_latency) ─────────────────
        service_times_s = [s.worker_latency_ms / 1000.0 for s in valid
                           if s.worker_latency_ms > 0]
        if not service_times_s:
            # Fall back to total latency if worker timestamps weren't set
            service_times_s = [s.total_latency_ms / 1000.0 for s in valid
                                if s.total_latency_ms > 0]
        if not service_times_s:
            return None

        mean_service = sum(service_times_s) / len(service_times_s)
        mu = 1.0 / mean_service  # μ — completions per second

        # ── Derived M/M/1 quantities ──────────────────────────────────────
        rho = lam / mu

        if rho >= 1.0:
            # Unstable: theoretical wait is infinite — report observed only
            wq_ms = float("inf")
            w_ms = float("inf")
            lq = float("inf")
            l_sys = float("inf")
        else:
            wq_s = rho / (mu * (1.0 - rho))       # Wq = ρ/(μ(1-ρ))
            w_s = wq_s + (1.0 / mu)               # W  = Wq + 1/μ
            lq = rho ** 2 / (1.0 - rho)           # Lq = ρ²/(1-ρ)
            l_sys = lam * w_s                     # Little's Law: L = λW
            wq_ms = wq_s * 1000.0
            w_ms = w_s * 1000.0

        # ── Empirical values from spans ───────────────────────────────────
        actual_waits = [s.queue_wait_ms for s in valid if s.queue_wait_ms >= 0]
        actual_systems = [s.total_latency_ms for s in valid if s.total_latency_ms > 0]

        actual_wq_ms = sum(actual_waits) / len(actual_waits) if actual_waits else 0.0
        actual_w_ms = sum(actual_systems) / len(actual_systems) if actual_systems else 0.0

        return QueueState(
            n_samples=len(valid),
            arrival_rate=lam,
            service_rate=mu,
            utilization=rho,
            theoretical_queue_wait_ms=wq_ms,
            theoretical_system_time_ms=w_ms,
            mean_queue_length=lq,
            mean_system_length=l_sys,
            actual_queue_wait_ms=actual_wq_ms,
            actual_system_time_ms=actual_w_ms,
            is_stable=rho < 1.0,
        )


# ── Regret Tracker ────────────────────────────────────────────────────────────

@dataclass
class RegretState:
    """
    Pseudo-regret statistics over a window of bandit episodes.

    Pseudo-regret: regret_t = oracle_mean_reward_t - actual_reward_t
    where oracle_mean_reward_t is the running mean of the empirically
    best action seen up to round t.

    Sublinear growth in cumulative_regret confirms the bandit is learning.
    """
    total_episodes: int

    cumulative_regret: float
    avg_regret_per_episode: float

    oracle_action_id: int
    oracle_action_name: str
    oracle_mean_reward: float

    action_mean_rewards: Dict[str, float]   # action_name → mean reward
    action_episode_counts: Dict[str, int]   # action_name → count

    # Theoretical LinUCB bound: O(d^0.5 * T^0.5 * log(T)^1.5)
    # Coefficient is empirical — useful for comparison, not as absolute guarantee
    theoretical_bound: float

    @property
    def is_sublinear(self) -> bool:
        """True if avg regret per episode is decreasing (bandit is improving)."""
        return (
            self.total_episodes > 1
            and self.avg_regret_per_episode < self.cumulative_regret / max(self.total_episodes, 1)
        )

    @property
    def regret_vs_bound_ratio(self) -> float:
        """How much of the theoretical budget has been used (< 1.0 is good)."""
        if self.theoretical_bound <= 0:
            return 0.0
        return self.cumulative_regret / self.theoretical_bound

    def display(self) -> str:
        action_lines = "  ".join(
            f"{name}:{reward:.3f}(n={self.action_episode_counts.get(name, 0)})"
            for name, reward in sorted(self.action_mean_rewards.items())
        ) or "—"
        ratio_str = f"{self.regret_vs_bound_ratio:.2f}×bound" if self.theoretical_bound > 0 else ""
        return "\n".join([
            f"┌─ REGRET (LinUCB, n={self.total_episodes}) ──────────────────────────────",
            f"│  Cumulative regret  {self.cumulative_regret:.4f}  "
            f"avg/episode={self.avg_regret_per_episode:.4f}  {ratio_str}",
            f"│  Oracle  action={self.oracle_action_name}  "
            f"mean_reward={self.oracle_mean_reward:.4f}",
            f"│  Per-action rewards  {action_lines}",
            f"└────────────────────────────────────────────────────────────────",
        ])


class RegretTracker:
    """
    Computes pseudo-regret from a list of bandit Episodes.

    Usage:
        episodes = reward_store.get_recent(200)
        state = RegretTracker.from_episodes(episodes)
        print(state.display())
    """

    @staticmethod
    def from_episodes(episodes: list) -> Optional[RegretState]:
        """
        episodes: list of Episode objects (from RewardStore.get_recent()).
        Returns None if no episodes.
        """
        if not episodes:
            return None

        T = len(episodes)

        # ── Per-action running statistics ─────────────────────────────────
        action_rewards: Dict[int, List[float]] = {a: [] for a in range(_N_ACTIONS)}
        for ep in episodes:
            aid = int(ep.action_id)
            if 0 <= aid < _N_ACTIONS:
                action_rewards[aid].append(float(ep.reward))

        action_mean: Dict[int, float] = {
            a: (sum(v) / len(v)) if v else 0.0
            for a, v in action_rewards.items()
        }

        oracle_id = max(action_mean, key=lambda a: action_mean[a])
        oracle_mean = action_mean[oracle_id]

        # ── Compute pseudo-regret per episode ─────────────────────────────
        # Use running oracle: at each step, oracle = best arm seen so far
        running_means: Dict[int, List[float]] = {a: [] for a in range(_N_ACTIONS)}
        cumulative_regret = 0.0

        for ep in episodes:
            aid = int(ep.action_id)
            if not (0 <= aid < _N_ACTIONS):
                continue

            # Oracle at this step = arm with highest mean reward so far
            oracle_now = max(
                (a for a in range(_N_ACTIONS) if running_means[a]),
                key=lambda a: sum(running_means[a]) / len(running_means[a]),
                default=aid,  # cold start: no regret on first episode
            )
            oracle_reward_now = (
                sum(running_means[oracle_now]) / len(running_means[oracle_now])
                if running_means[oracle_now] else float(ep.reward)
            )

            regret_t = max(0.0, oracle_reward_now - float(ep.reward))
            cumulative_regret += regret_t

            running_means[aid].append(float(ep.reward))

        avg_regret = cumulative_regret / T if T > 0 else 0.0

        # ── Theoretical LinUCB bound ──────────────────────────────────────
        # From Li et al. 2010: regret ≤ O(√(d·T·log³(KT/δ)))
        # Simplified upper bound with d=10, K=4, δ=0.05, constant=2.0
        d = _N_FEATURES
        if T > 1:
            log_factor = math.log(T) ** 1.5
            theoretical_bound = 2.0 * math.sqrt(d * T) * log_factor
        else:
            theoretical_bound = 0.0

        # ── Format output ─────────────────────────────────────────────────
        action_mean_by_name = {
            ACTION_NAMES.get(a, str(a)): v
            for a, v in action_mean.items()
            if action_rewards[a]  # only show arms that have been tried
        }
        action_counts_by_name = {
            ACTION_NAMES.get(a, str(a)): len(v)
            for a, v in action_rewards.items()
        }

        return RegretState(
            total_episodes=T,
            cumulative_regret=cumulative_regret,
            avg_regret_per_episode=avg_regret,
            oracle_action_id=oracle_id,
            oracle_action_name=ACTION_NAMES.get(oracle_id, str(oracle_id)),
            oracle_mean_reward=oracle_mean,
            action_mean_rewards=action_mean_by_name,
            action_episode_counts=action_counts_by_name,
            theoretical_bound=theoretical_bound,
        )
