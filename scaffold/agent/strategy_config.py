"""
StrategyConfig — Learnable Prompt Strategy Selector.

AWOS runs each coding task using one of four prompt styles. A UCB bandit
tracks which style wins most often per task type and routes automatically.

Strategies (4):
  direct       — no scaffolding, pure task execution (baseline)
  cot          — chain-of-thought: reason before writing code
  step_by_step — numbered action steps before SEARCH/REPLACE
  rubber_duck  — explain to a junior dev, then implement

StrategyRouter maintains per (task_type × strategy) UCB accumulators
persisted to .awos/strategy_weights.json.
"""

from __future__ import annotations

import json
import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_WEIGHTS_PATH = Path(".awos") / "strategy_weights.json"


# ── Strategy definitions ───────────────────────────────────────────────────

@dataclass(frozen=True)
class StrategyConfig:
    """A named prompt variant injected into the Worker prompt."""
    name: str
    preamble: str          # prepended before the TASK line
    instruction_suffix: str  # appended before the OUTPUT FORMAT block


STRATEGIES: dict[str, StrategyConfig] = {
    "direct": StrategyConfig(
        name="direct",
        preamble="",
        instruction_suffix="",
    ),
    "cot": StrategyConfig(
        name="cot",
        preamble=(
            "Before writing any code, think through the problem step by step.\n"
            "Identify exactly which lines need to change and why.\n\n"
        ),
        instruction_suffix=(
            "\nWrite your reasoning FIRST (2-3 sentences), then the SEARCH/REPLACE."
        ),
    ),
    "step_by_step": StrategyConfig(
        name="step_by_step",
        preamble=(
            "Break this task into numbered micro-steps, then execute them in order.\n\n"
        ),
        instruction_suffix=(
            "\nList your steps first (e.g. 1. Find X  2. Replace Y), then the code."
        ),
    ),
    "rubber_duck": StrategyConfig(
        name="rubber_duck",
        preamble=(
            "Explain the section of code that needs to change as if teaching a junior developer "
            "what it currently does. Then implement the change.\n\n"
        ),
        instruction_suffix="",
    ),
}

_STRATEGY_NAMES = list(STRATEGIES.keys())

# Task type classifier tokens
_TASK_TYPE_SIGNALS = {
    "bug_fix":      ("fix", "bug", "error", "crash", "fail", "broken", "wrong", "incorrect"),
    "refactor":     ("refactor", "rename", "move", "extract", "clean", "simplify", "reorganise"),
    "new_feature":  ("add", "create", "implement", "new", "build", "write", "introduce"),
    "architecture": ("architect", "design", "system", "module", "interface", "abstraction", "layer"),
}
TASK_TYPES = list(_TASK_TYPE_SIGNALS.keys()) + ["other"]


# ── StrategyRouter ─────────────────────────────────────────────────────────

class StrategyRouter:
    """
    UCB-1 bandit over (task_type × strategy) pairs.

    Selection:
        score(t, s) = μ(t,s) + C * sqrt(log(N_t) / n(t,s))
        where C = sqrt(2) (standard UCB-1 constant)

    Cold start: any (task_type, strategy) pair with 0 observations
    is selected immediately (infinite UCB score) to ensure exploration.

    Persistence: counts and sums saved to .awos/strategy_weights.json
    after every update.
    """

    C = math.sqrt(2)   # UCB-1 exploration constant

    def __init__(self, weights_path: Optional[Path] = None) -> None:
        self._path = weights_path or _DEFAULT_WEIGHTS_PATH
        # counts[(task_type, strategy)] → int
        self._counts: dict[tuple[str, str], int] = defaultdict(int)
        # wins[(task_type, strategy)]   → float (cumulative reward)
        self._wins: dict[tuple[str, str], float] = defaultdict(float)
        self._load()

    # ── Public API ──────────────────────────────────────────────────────

    def classify(self, task: dict) -> str:
        """Map a task dict to one of TASK_TYPES."""
        action = task.get("action", "").lower()
        for task_type, signals in _TASK_TYPE_SIGNALS.items():
            if any(sig in action for sig in signals):
                return task_type
        return "other"

    def select(self, task: dict) -> StrategyConfig:
        """
        Select the best strategy for this task via UCB-1.
        Unexplored (task_type, strategy) pairs are prioritised.
        """
        task_type = self.classify(task)
        total = sum(self._counts[(task_type, s)] for s in _STRATEGY_NAMES) + 1

        best_name: Optional[str] = None
        best_score = -math.inf

        for s in _STRATEGY_NAMES:
            n = self._counts[(task_type, s)]
            if n == 0:
                logger.debug("[strategy] cold-start explore: task_type=%s strategy=%s", task_type, s)
                return STRATEGIES[s]   # immediate exploration

            mu = self._wins[(task_type, s)] / n
            ucb = mu + self.C * math.sqrt(math.log(total) / n)
            if ucb > best_score:
                best_score, best_name = ucb, s

        chosen = STRATEGIES[best_name]  # type: ignore[arg-type]
        logger.debug(
            "[strategy] task_type=%s → %s (score=%.3f)",
            task_type, chosen.name, best_score,
        )
        return chosen

    def update(self, task: dict, strategy_name: str, reward: float) -> None:
        """
        Record outcome for (task_type, strategy_name).
        reward should be the same computed reward used for LinUCB.
        """
        task_type = self.classify(task)
        k = (task_type, strategy_name)
        self._counts[k] += 1
        self._wins[k] += reward
        self._save()
        logger.debug(
            "[strategy] updated (%s, %s): count=%d  cum_reward=%.3f",
            task_type, strategy_name, self._counts[k], self._wins[k],
        )

    def summary(self) -> dict:
        """Human-readable win rates per (task_type, strategy)."""
        out: dict = {}
        for task_type in TASK_TYPES:
            out[task_type] = {}
            for s in _STRATEGY_NAMES:
                k = (task_type, s)
                n = self._counts[k]
                mu = (self._wins[k] / n) if n > 0 else None
                out[task_type][s] = {"count": n, "mean_reward": round(mu, 4) if mu is not None else None}
        return out

    # ── Persistence ─────────────────────────────────────────────────────

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "counts": {f"{t}|{s}": v for (t, s), v in self._counts.items()},
            "wins":   {f"{t}|{s}": v for (t, s), v in self._wins.items()},
        }
        with self._path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with self._path.open(encoding="utf-8") as f:
                payload = json.load(f)
            for key, val in payload.get("counts", {}).items():
                t, s = key.split("|", 1)
                self._counts[(t, s)] = int(val)
            for key, val in payload.get("wins", {}).items():
                t, s = key.split("|", 1)
                self._wins[(t, s)] = float(val)
            logger.info("[strategy] loaded weights from %s", self._path)
        except Exception as exc:
            logger.warning("[strategy] could not load weights: %s — starting fresh", exc)
