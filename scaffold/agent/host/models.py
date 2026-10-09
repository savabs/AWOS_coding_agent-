"""
models.py — the shared M1 host contract: JobSpec and the storage layout.

Spec: docs/specs/host_queue.md. Other host modules (journal, handoff, ops) may
import from here or define a structurally compatible type of their own.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

QUEUED, RUNNING, PAUSED = "queued", "running", "paused"
DONE, FAILED, CANCELLED = "done", "failed", "cancelled"
STATES = (QUEUED, RUNNING, PAUSED, DONE, FAILED, CANCELLED)
FINAL_STATES = (DONE, FAILED, CANCELLED)

KINDS = ("coding",)
PRIVACY = ("local_only", "cloud_ok")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def host_root() -> Path:
    """AWOS_HOST_DIR, default ~/.awos/host/ (created on demand by callers)."""
    raw = os.getenv("AWOS_HOST_DIR", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".awos" / "host"


def job_dir(job_id: str, root: Optional[Path] = None) -> Path:
    return (root or host_root()) / "jobs" / job_id


@dataclass
class JobSpec:
    id: str
    goal: str
    repo_path: str
    kind: str = "coding"
    budget_usd: float = 1.0
    privacy: str = "cloud_ok"
    created_at: str = field(default_factory=now_iso)
    state: str = QUEUED
    attempts: int = 0
    result: Optional[dict] = None
    # Bookkeeping beyond the contract (all optional).
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None
    cancel_requested: bool = False
    worker_pid: Optional[int] = None
    worker_started: Optional[str] = None
    child_pid: Optional[int] = None
    child_started: Optional[str] = None

    @staticmethod
    def new(goal: str, repo_path: str | Path, kind: str = "coding",
            budget_usd: float = 1.0, privacy: str = "cloud_ok") -> "JobSpec":
        """Validate and build a queued job. The goal is kept verbatim."""
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("a job needs a goal")
        if kind not in KINDS:
            raise ValueError(f"unknown kind {kind!r}; available: {', '.join(KINDS)}")
        if privacy not in PRIVACY:
            raise ValueError(f"privacy must be one of {', '.join(PRIVACY)}")
        budget = float(budget_usd)
        if budget < 0:
            raise ValueError("budget_usd must be >= 0")
        repo = Path(repo_path).expanduser().resolve()
        if not repo.is_dir():
            raise ValueError(f"not a directory: {repo}")
        return JobSpec(id=uuid.uuid4().hex, goal=goal, repo_path=str(repo),
                       kind=kind, budget_usd=budget, privacy=privacy)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "JobSpec":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)
