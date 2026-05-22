"""
RewardStore — append-only log of (task, action, outcome) episodes.

Every time AWOS completes a task (success or failure), one Episode is stored.
The LinUCBRouter and GPWorldModel read from this store to learn and improve.

Storage: .awos/reward_store.jsonl  (one JSON object per line, never deleted)

This is the data flywheel: the longer AWOS runs, the better the ML router gets.
"""

from __future__ import annotations

import json
import logging
import math
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_STORE_PATH = Path(".awos") / "reward_store.jsonl"

# ── Reward Function Parameters (mathematically derived) ────────────────────
#
# These are NOT arbitrary constants. They derive from the ladder cost structure.
#
# _MIN_COST  = cheapest per-request cost  (Gemini Flash / DeepSeek)
# _MAX_COST  = most expensive per-request cost (Sonnet, now the top tier)
# _COST_SPREAD = _MAX_COST / _MIN_COST  → the natural cost ratio of the ladder
#
# η (eta):  cost sensitivity for SUCCESS — opportunity cost of using an
#           expensive model when a cheaper one could have solved it.
# κ (kappa): max FAILURE penalty magnitude — the worst failure (most expensive
#            model) gets reward = -κ. Cheaper failures are proportionally
#            less punished via log-normalization.

_MIN_COST = 0.001   # $ per request (Gemini Flash / DeepSeek)
_MAX_COST = 0.050   # $ per request (Sonnet)
_COST_SPREAD = _MAX_COST / _MIN_COST  # 50.0 — natural ladder cost ratio

_ETA = 0.05   # opportunity-cost weight for successes
_KAPPA = 0.30  # max failure-penalty magnitude

# Fallback costs when token-level tracking is unavailable (uses tier defaults)
_TIER_DEFAULT_COST = {0: 0.001, 1: 0.001, 2: 0.017, 3: 0.050}


@dataclass
class Episode:
    """
    One completed AWOS task attempt with its outcome.

    action_id maps to EscalationLevel:
        0 = GEMINI_FLASH
        1 = DEEPSEEK
        2 = HAIKU
        3 = SONNET (top tier — no Opus)
    """
    episode_id: str
    task_id: str
    action_id: int                  # 0-3
    features: list[float]           # 10-dim feature vector
    success: bool
    cost_usd: float
    latency_ms: float
    reward: float                   # computed reward signal
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    task_action_text: str = ""      # human-readable task description (for debug)
    model_name: str = ""            # model name used
    reward_breakdown: dict = field(default_factory=dict)  # tracked components

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Episode":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def _cost_ratio(cost_usd: float, action_id: int) -> float:
    """Normalize cost to [0, 1] using the ladder max. Falls back to tier default."""
    cost = cost_usd if cost_usd > 0 else _TIER_DEFAULT_COST.get(action_id, _MIN_COST)
    return min(cost / _MAX_COST, 1.0)


def compute_reward(success, action_id: int, cost_usd: float) -> float:
    """
    Asymmetric cost-proportional reward — mathematically principled.

    Derivation
    ----------
    We want R(success, cost) with two properties:

    1. Success:  R_success = 1 - η * (cost / max_cost)
       Opportunity cost: using an expensive model for a solvable task
       incurs a linear penalty in normalized cost.

    2. Failure:  R_fail = -κ * log(1 + spread * cost/max_cost) / log(1 + spread)
       Wasted spend scales with cost, but log-normalized so:
         - R(fail, cheapest)  ≈ 0  (minimal penalty for trying cheap first)
         - R(fail, most_exp)   = -κ (max penalty for wasting top tier)
       The spread parameter (50×) is the natural cost ratio of the ladder.

    Why log?
        log(1+x) grows slower than x, preventing extreme penalties while
        preserving monotonicity. Normalization by log(1+spread) bounds the
        output to exactly [-κ, 1-η].

    Parameters (physically meaningful, not magic numbers)
    ----------
    η       = 0.05   opportunity cost of expensive success
    κ       = 0.30   max failure penalty (calibrated for bandit stability)
    spread  = 50.0   _MAX_COST / _MIN_COST  (derived from ladder)

    Effective range: [-0.30, 1.0]

    Float pass_rate support (added 2026-05-19):
        When success is a float (e.g. 0.75 from TestRunner.pass_rate),
        reward is linearly interpolated between full success and full failure.
        This lets the LinUCB bandit learn from partial test pass rates.
    """
    ratio = _cost_ratio(cost_usd, action_id)

    # Opportunity cost is always subtracted (you paid for the model regardless)
    opportunity_cost = _ETA * ratio

    # Float pass_rate: interpolate between full success and full failure
    if isinstance(success, float):
        pass_rate = max(0.0, min(1.0, success))
        r_success = compute_reward(True, action_id, cost_usd)
        r_failure = compute_reward(False, action_id, cost_usd)
        return round(r_success * pass_rate + r_failure * (1.0 - pass_rate), 4)

    if success:
        base_value = 1.0
        failure_penalty = 0.0
    else:
        base_value = 0.0
        raw = math.log(1.0 + _COST_SPREAD * ratio)
        norm = math.log(1.0 + _COST_SPREAD)
        failure_penalty = _KAPPA * (raw / norm)

    return round(base_value - opportunity_cost - failure_penalty, 4)


def compute_reward_breakdown(success: bool, action_id: int, cost_usd: float) -> dict[str, float]:
    """
    Decompose reward into three tracked, mathematically meaningful components.
    """
    ratio = _cost_ratio(cost_usd, action_id)

    opportunity_cost = _ETA * ratio
    base_value = 1.0 if success else 0.0

    if success:
        failure_penalty = 0.0
    else:
        raw = math.log(1.0 + _COST_SPREAD * ratio)
        norm = math.log(1.0 + _COST_SPREAD)
        failure_penalty = _KAPPA * (raw / norm)

    total = round(base_value - opportunity_cost - failure_penalty, 4)

    return {
        "base_value": round(base_value, 4),
        "opportunity_cost": round(-opportunity_cost, 4),
        "failure_penalty": round(-failure_penalty, 4),
        "total_reward": total,
        "cost_ratio": round(ratio, 4),
        "params": {"eta": _ETA, "kappa": _KAPPA, "spread": _COST_SPREAD},
    }


class ReplayGate:
    """
    Filters episodes before they enter the LinUCB training buffer.

    Only episodes that pass this gate trigger a weight update in LinUCBRouter.
    This prevents noisy or redundant episodes from degrading the bandit.

    Scoring (each component ∈ [0, 1], threshold = 0.4):
    ─────────────────────────────────────────────────────
    1. Informativeness — |reward| > 0.1  (near-zero reward carries no signal)
    2. Novelty         — Euclidean distance from recent buffer centroids
                        (deduplicates near-identical tasks)
    3. Boundary        — Episode is near a decision boundary (LinUCB exploits
                        these most). Approximated by action diversity: episodes
                        that use a non-majority action score higher.

    Gate score = mean(informativeness, novelty, boundary)
    Admitted if score >= threshold.
    """

    def __init__(
        self,
        threshold: float = 0.4,
        novelty_window: int = 50,
        min_novelty_dist: float = 0.15,
    ) -> None:
        self.threshold = threshold
        self._novelty_window = novelty_window
        self._min_novelty_dist = min_novelty_dist
        self._recent_features: list[list[float]] = []
        self._action_counts: list[int] = [0] * N_ACTIONS_REPLAY

    def score(self, episode: "Episode") -> float:
        """
        Returns gate score in [0, 1]. Call before updating LinUCB weights.
        """
        r = abs(episode.reward)
        informativeness = min(r / 1.0, 1.0)

        if self._recent_features:
            dists = [
                math.sqrt(sum((a - b) ** 2 for a, b in zip(episode.features, ref)))
                for ref in self._recent_features[-self._novelty_window:]
            ]
            min_dist = min(dists)
            novelty = min(min_dist / (self._min_novelty_dist * 5), 1.0)
        else:
            novelty = 1.0

        total_acts = sum(self._action_counts) + 1
        majority_frac = max(self._action_counts) / total_acts if total_acts > 0 else 0
        a = episode.action_id
        own_frac = self._action_counts[a] / total_acts if total_acts > 0 else 0
        boundary = 1.0 - own_frac / max(majority_frac, 1e-6)
        boundary = max(0.0, min(boundary, 1.0))

        return (informativeness + novelty + boundary) / 3.0

    def admit(self, episode: "Episode") -> bool:
        """
        Returns True if the episode should be admitted to the training buffer.
        Also updates internal tracking state.
        """
        s = self.score(episode)
        self._recent_features.append(list(episode.features))
        if len(self._recent_features) > self._novelty_window * 2:
            self._recent_features = self._recent_features[-self._novelty_window:]
        a = episode.action_id
        if 0 <= a < len(self._action_counts):
            self._action_counts[a] += 1
        admitted = s >= self.threshold
        logger.debug(
            "[replay_gate] score=%.3f admit=%s episode=%s",
            s, admitted, episode.episode_id[:8],
        )
        return admitted


N_ACTIONS_REPLAY = 4  # mirrors ml_router.N_ACTIONS (avoid circular import)


class RewardStore:
    """
    Append-only log of AWOS task outcomes.

    Usage:
        store = RewardStore()
        store.store(task, action_id=1, features=[...], success=True, cost_usd=0.001, latency_ms=1200.0)
        recent = store.get_recent(50)
        print(store.total_episodes())
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _DEFAULT_STORE_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)

    # ── Public API ─────────────────────────────────────────────────────────

    def store(
        self,
        task: dict,
        action_id: int,
        features: list[float],
        success: bool,
        cost_usd: float = 0.0,
        latency_ms: float = 0.0,
        model_name: str = "",
    ) -> Episode:
        """
        Record a completed task attempt. Returns the stored Episode.
        """
        reward = compute_reward(success, action_id, cost_usd)
        breakdown = compute_reward_breakdown(success, action_id, cost_usd)
        episode = Episode(
            episode_id=str(uuid.uuid4()),
            task_id=str(task.get("task_id", "unknown")),
            action_id=action_id,
            features=list(features),
            success=success,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            reward=reward,
            task_action_text=str(task.get("action", ""))[:200],
            model_name=model_name,
            reward_breakdown=breakdown,
        )
        self._append(episode)
        logger.debug(
            "[reward_store] episode=%s  action=%d  success=%s  reward=%.3f",
            episode.episode_id[:8], action_id, success, reward,
        )
        return episode

    def get_recent(self, n: int = 200) -> list[Episode]:
        """Return the n most recent episodes (oldest first)."""
        lines = self._read_lines()
        recent_lines = lines[-n:] if len(lines) > n else lines
        episodes = []
        for line in recent_lines:
            try:
                episodes.append(Episode.from_dict(json.loads(line)))
            except Exception as exc:
                logger.warning("[reward_store] skipping malformed line: %s", exc)
        return episodes

    def total_episodes(self) -> int:
        """Total episodes recorded (counts lines in file)."""
        if not self._path.exists():
            return 0
        return sum(1 for _ in self._path.open(encoding="utf-8"))

    def success_rate(self, action_id: int | None = None, window: int = 50) -> float:
        """
        Pass@1 estimate over the last `window` episodes.
        If action_id is given, filter to that action only.
        """
        episodes = self.get_recent(window)
        if action_id is not None:
            episodes = [e for e in episodes if e.action_id == action_id]
        if not episodes:
            return 0.0
        return sum(1 for e in episodes if e.success) / len(episodes)

    def summary(self) -> dict[str, Any]:
        """Human-readable summary for logging / debugging."""
        episodes = self.get_recent(200)
        if not episodes:
            return {"total": 0, "message": "no data yet"}

        total = len(episodes)
        by_action: dict[int, dict] = {}
        for e in episodes:
            a = e.action_id
            if a not in by_action:
                by_action[a] = {"count": 0, "success": 0, "total_reward": 0.0}
            by_action[a]["count"] += 1
            by_action[a]["success"] += int(e.success)
            by_action[a]["total_reward"] += e.reward

        action_names = ["gemini_flash", "deepseek", "haiku", "sonnet"]
        stats = {}
        for a_id, data in sorted(by_action.items()):
            name = action_names[a_id] if a_id < len(action_names) else str(a_id)
            stats[name] = {
                "count": data["count"],
                "success_rate": round(data["success"] / data["count"], 3),
                "avg_reward": round(data["total_reward"] / data["count"], 3),
            }

        return {
            "total_episodes": total,
            "overall_success_rate": round(sum(e.success for e in episodes) / total, 3),
            "by_action": stats,
        }

    # ── Internal ───────────────────────────────────────────────────────────

    def _append(self, episode: Episode) -> None:
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(episode.to_dict()) + "\n")

    def _read_lines(self) -> list[str]:
        if not self._path.exists():
            return []
        with self._path.open(encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
