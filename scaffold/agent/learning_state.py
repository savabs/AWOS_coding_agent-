"""
learning_state.py — Durable self-learning session state and KPI windows.

Persists across Orchestrator instances so prompt evolution triggers on real
session volume, not in-memory counters.

Files:
  .awos/learning_state.json     — counters, candidate probation, recent KPIs
  .awos/prompt_versions/        — versioned prompt payloads (managed by PromptEvolver)
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

_STATE_FILE = "learning_state.json"
_MAX_RECENT_SESSIONS = 50


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class SessionKPI:
    success: bool
    cost_usd: float
    tasks_failed: int
    tasks_total: int
    used_candidate_prompt: bool = False
    recorded_at: str = field(default_factory=_now_iso)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionKPI:
        return cls(
            success=bool(data.get("success")),
            cost_usd=float(data.get("cost_usd", 0.0)),
            tasks_failed=int(data.get("tasks_failed", 0)),
            tasks_total=int(data.get("tasks_total", 0)),
            used_candidate_prompt=bool(data.get("used_candidate_prompt", False)),
            recorded_at=data.get("recorded_at", _now_iso()),
        )


@dataclass
class KPIWindow:
    success_rate: float = 0.0
    cost_per_task: float = 0.0
    failure_rate: float = 0.0
    n: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _compute_kpi(sessions: list[SessionKPI]) -> KPIWindow:
    if not sessions:
        return KPIWindow()
    n = len(sessions)
    successes = sum(1 for s in sessions if s.success)
    total_cost = sum(s.cost_usd for s in sessions)
    total_tasks = sum(max(s.tasks_total, 1) for s in sessions)
    failed_tasks = sum(s.tasks_failed for s in sessions)
    return KPIWindow(
        success_rate=round(successes / n, 4),
        cost_per_task=round(total_cost / total_tasks, 6),
        failure_rate=round(failed_tasks / max(total_tasks, 1), 4),
        n=n,
    )


class LearningState:
    """Durable learning loop state — survives process restarts."""

    def __init__(self, store_path: str = ".awos") -> None:
        self._root = Path(store_path)
        self._path = self._root / _STATE_FILE
        self._root.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    # ── Public API ───────────────────────────────────────────────────────────

    @property
    def sessions_completed(self) -> int:
        return int(self._data.get("sessions_completed", 0))

    @property
    def active_version(self) -> int:
        return int(self._data.get("active_version", 0))

    def has_candidate(self) -> bool:
        return self._data.get("candidate") is not None

    def candidate_info(self) -> Optional[dict[str, Any]]:
        cand = self._data.get("candidate")
        return dict(cand) if isinstance(cand, dict) else None

    def record_session(
        self,
        *,
        success: bool,
        cost_usd: float,
        tasks_failed: int,
        tasks_total: int,
        used_candidate_prompt: bool = False,
    ) -> int:
        """Append session KPI and increment durable counter. Returns new total."""
        entry = SessionKPI(
            success=success,
            cost_usd=max(0.0, float(cost_usd)),
            tasks_failed=int(tasks_failed),
            tasks_total=max(0, int(tasks_total)),
            used_candidate_prompt=used_candidate_prompt,
        )
        recent: list[dict[str, Any]] = list(self._data.get("recent_sessions", []))
        recent.append(asdict(entry))
        self._data["recent_sessions"] = recent[-_MAX_RECENT_SESSIONS:]
        self._data["sessions_completed"] = self.sessions_completed + 1
        self._save()
        return self.sessions_completed

    def start_candidate_probation(
        self,
        *,
        version: int,
        accept_window: Optional[int] = None,
    ) -> KPIWindow:
        """Begin evaluating a new prompt version against recent baseline."""
        window = accept_window or int(os.getenv("AWOS_PROMPT_ACCEPT_WINDOW", "5"))
        baseline_sessions = self._recent_before_candidate()
        baseline_kpi = _compute_kpi(baseline_sessions)
        self._data["candidate"] = {
            "version": version,
            "started_session": self.sessions_completed,
            "accept_window": window,
            "sessions_evaluated": 0,
            "baseline_kpi": baseline_kpi.to_dict(),
        }
        self._data["last_evolution_session"] = self.sessions_completed
        self._save()
        return baseline_kpi

    def record_candidate_session(self) -> int:
        """Increment candidate evaluation counter. Returns sessions_evaluated."""
        cand = self._data.get("candidate")
        if not cand:
            return 0
        cand["sessions_evaluated"] = int(cand.get("sessions_evaluated", 0)) + 1
        self._data["candidate"] = cand
        self._save()
        return int(cand["sessions_evaluated"])

    def maybe_finalize_candidate(self) -> Optional[Literal["accepted", "rejected"]]:
        """
        If candidate probation window is complete, compare KPIs and return verdict.
        Does not mutate prompt files — caller applies accept/rollback.
        """
        cand = self._data.get("candidate")
        if not cand:
            return None
        needed = int(cand.get("accept_window", 5))
        evaluated = int(cand.get("sessions_evaluated", 0))
        if evaluated < needed:
            return None

        baseline = KPIWindow(**cand.get("baseline_kpi", {}))
        candidate_sessions = self._candidate_evaluation_sessions(needed)
        candidate_kpi = _compute_kpi(candidate_sessions)

        if self._accept_candidate(baseline, candidate_kpi):
            self._data["active_version"] = int(cand["version"])
            self._data["candidate"] = None
            self._data["last_accepted_version"] = int(cand["version"])
            self._data["last_candidate_kpi"] = candidate_kpi.to_dict()
            self._save()
            return "accepted"

        self._data["candidate"] = None
        self._data["last_rejected_version"] = int(cand["version"])
        self._data["last_candidate_kpi"] = candidate_kpi.to_dict()
        self._save()
        return "rejected"

    def set_active_version(self, version: int) -> None:
        self._data["active_version"] = int(version)
        self._save()

    def summary(self) -> dict[str, Any]:
        recent = [SessionKPI.from_dict(s) for s in self._data.get("recent_sessions", [])]
        return {
            "sessions_completed": self.sessions_completed,
            "active_version": self.active_version,
            "last_evolution_session": self._data.get("last_evolution_session"),
            "has_candidate": self.has_candidate(),
            "candidate": self.candidate_info(),
            "recent_kpi": _compute_kpi(recent[-10:]).to_dict() if recent else {},
            "last_accepted_version": self._data.get("last_accepted_version"),
            "last_rejected_version": self._data.get("last_rejected_version"),
        }

    # ── Internal ─────────────────────────────────────────────────────────────

    def _accept_candidate(self, baseline: KPIWindow, candidate: KPIWindow) -> bool:
        if candidate.n == 0:
            return False

        max_cost_increase = float(os.getenv("AWOS_PROMPT_MAX_COST_INCREASE", "0.25"))
        max_success_drop = float(os.getenv("AWOS_PROMPT_MIN_SUCCESS_DROP", "0.05"))

        if baseline.n == 0:
            return True

        success_ok = candidate.success_rate >= (baseline.success_rate - max_success_drop)
        if baseline.cost_per_task <= 0:
            cost_ok = True
        else:
            cost_ok = candidate.cost_per_task <= baseline.cost_per_task * (1.0 + max_cost_increase)

        return success_ok and cost_ok

    def _recent_before_candidate(self) -> list[SessionKPI]:
        recent = [SessionKPI.from_dict(s) for s in self._data.get("recent_sessions", [])]
        window = int(os.getenv("AWOS_PROMPT_ACCEPT_WINDOW", "5"))
        if len(recent) <= 1:
            return recent
        # Exclude the session that just completed (last entry) from baseline.
        prior = recent[:-1]
        return prior[-window:]

    def _candidate_evaluation_sessions(self, window: int) -> list[SessionKPI]:
        recent = [SessionKPI.from_dict(s) for s in self._data.get("recent_sessions", [])]
        return [s for s in recent if s.used_candidate_prompt][-window:]

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {
                "sessions_completed": 0,
                "active_version": 0,
                "last_evolution_session": 0,
                "recent_sessions": [],
            }
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {
                "sessions_completed": 0,
                "active_version": 0,
                "last_evolution_session": 0,
                "recent_sessions": [],
            }

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._data, indent=2) + "\n", encoding="utf-8")
