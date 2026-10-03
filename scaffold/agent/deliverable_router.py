"""
deliverable_router.py — Optional coarse hints for ML features (NOT path selection).

Path selection is owned by the planner (see plan_actions.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class TaskKind(str, Enum):
    CREATE = "create"
    MUTATE = "mutate"
    UNKNOWN = "unknown"


_CREATE_VERBS = ("create", "write", "generate", "scaffold", "new file", "make a", "make an")
_MUTATE_VERBS = ("fix ", "refactor", "patch ", "debug ", "modify ", "update the code")
_CODE_MUTATION = re.compile(
    r"\b(add|implement|insert)\s+(a\s+)?(function|method|class|def|test)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DeliverableDecision:
    """Coarse signal only — used for logging / future LinUCB features."""

    kind: TaskKind
    reason: str = ""
    confidence: float = 0.0


class DeliverableRouter:
    """Free coarse classifier. Does NOT choose file paths."""

    def classify(self, goal: str) -> DeliverableDecision:
        goal = (goal or "").strip()
        if not goal:
            return DeliverableDecision(TaskKind.UNKNOWN, reason="empty goal")

        g = goal.lower()
        if any(v in g for v in _MUTATE_VERBS) or _CODE_MUTATION.search(goal):
            return DeliverableDecision(TaskKind.MUTATE, reason="mutate signals", confidence=0.8)
        if any(v in g for v in _CREATE_VERBS) or re.search(r"\.\w{2,4}\b", g):
            return DeliverableDecision(TaskKind.CREATE, reason="create signals", confidence=0.7)
        return DeliverableDecision(TaskKind.UNKNOWN, reason="no strong signal")
