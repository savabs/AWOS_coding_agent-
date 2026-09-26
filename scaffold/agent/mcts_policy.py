"""
mcts_policy.py — When to run MCTS search on the AWOS hot path.

Default: ON after worker failure for medium/high complexity tasks.
Opt-out: AWOS_MCTS_DISABLE=true
Legacy opt-in env vars still honored: AWOS_USE_MCTS, AWOS_MCTS=1
"""

from __future__ import annotations

import os
from typing import Any


def mcts_enabled() -> bool:
    """Controls whether MCTS fallback runs after worker failure."""
    if os.getenv("AWOS_MCTS_DISABLE", "").lower() in ("1", "true", "yes"):
        return False
    if os.getenv("AWOS_USE_MCTS", "").lower() in ("1", "true", "yes"):
        return True
    if os.getenv("AWOS_MCTS", "").lower() in ("1", "true", "yes"):
        return True
    return os.getenv("AWOS_MCTS_DEFAULT", "true").lower() in ("1", "true", "yes")


def should_run_mcts_fallback(task: dict[str, Any], worker_failed: bool) -> bool:
    """
    Run MCTS after worker gives up — search + verify, not duplicate planning.

    Skips low-complexity tasks unless AWOS_MCTS_ON_LOW=true.
    """
    if not worker_failed or not mcts_enabled():
        return False
    complexity = (task.get("complexity") or "medium").lower()
    if complexity == "low":
        return os.getenv("AWOS_MCTS_ON_LOW", "").lower() in ("1", "true", "yes")
    return True


def mcts_rollout_budget(task: dict[str, Any]) -> int:
    """Rollout budget scaled by task complexity."""
    complexity = (task.get("complexity") or "medium").lower()
    if complexity == "high":
        return int(os.getenv("AWOS_MCTS_ROLLOUTS", "8"))
    if complexity == "low":
        return int(os.getenv("AWOS_MCTS_ROLLOUTS_LOW", "4"))
    return int(os.getenv("AWOS_MCTS_ROLLOUTS_MEDIUM", "6"))
