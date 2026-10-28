"""
runtime_session.py — Durable execution sessions for multi-hour AWOS goals.

Persists unified session state at .awos/sessions/rs_<id>.json so runs can
pause, resume, fork, and checkpoint without losing plan progress or sandbox context.

Spec: docs/specs/runtime_session_spec.md
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

SESSION_ID_RE = re.compile(r"^rs_[0-9a-f]{12}$")
_SCHEMA_VERSION = 1


class SessionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class SessionProgress:
    completed_task_ids: list[Any] = field(default_factory=list)
    failed_task_ids: list[Any] = field(default_factory=list)
    current_task_id: Optional[Any] = None
    total_tasks: int = 0
    decomposition_depth: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> SessionProgress:
        if not data:
            return cls()
        return cls(
            completed_task_ids=list(data.get("completed_task_ids", [])),
            failed_task_ids=list(data.get("failed_task_ids", [])),
            current_task_id=data.get("current_task_id"),
            total_tasks=int(data.get("total_tasks", 0)),
            decomposition_depth=int(data.get("decomposition_depth", 0)),
        )


@dataclass
class SessionSandbox:
    enabled: bool = False
    feature_id: Optional[str] = None
    worktree_path: Optional[str] = None
    branch: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> SessionSandbox:
        if not data:
            return cls()
        return cls(
            enabled=bool(data.get("enabled", False)),
            feature_id=data.get("feature_id"),
            worktree_path=data.get("worktree_path"),
            branch=data.get("branch"),
        )


@dataclass
class RuntimeSession:
    schema_version: int = _SCHEMA_VERSION
    session_id: str = ""
    goal: str = ""
    status: SessionStatus = SessionStatus.PENDING
    created_at: str = ""
    updated_at: str = ""
    paused_at: Optional[str] = None
    codebase_root: str = "."
    reasoning_session_id: Optional[str] = None
    goal_graph_id: Optional[str] = None
    progress: SessionProgress = field(default_factory=SessionProgress)
    budget: dict[str, float] = field(default_factory=lambda: {"spent_usd": 0.0})
    sandbox: SessionSandbox = field(default_factory=SessionSandbox)
    parent_session_id: Optional[str] = None
    cancel_requested: bool = False
    goal_hash: str = ""
    pause_reason: Optional[str] = None  # "STAGNATION", "BUDGET", "USER", etc.

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "goal": self.goal,
            "goal_hash": self.goal_hash or goal_hash(self.goal),
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "paused_at": self.paused_at,
            "codebase_root": self.codebase_root,
            "reasoning_session_id": self.reasoning_session_id,
            "goal_graph_id": self.goal_graph_id,
            "progress": self.progress.to_dict(),
            "budget": dict(self.budget),
            "sandbox": self.sandbox.to_dict(),
            "parent_session_id": self.parent_session_id,
            "cancel_requested": self.cancel_requested,
            "pause_reason": self.pause_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeSession:
        status_raw = data.get("status", SessionStatus.PENDING.value)
        try:
            status = SessionStatus(status_raw)
        except ValueError:
            status = SessionStatus.PENDING

        budget = data.get("budget") or {}
        spent = budget.get("spent_usd", 0.0)

        goal = data.get("goal", "")
        return cls(
            schema_version=int(data.get("schema_version", _SCHEMA_VERSION)),
            session_id=data.get("session_id", ""),
            goal=goal,
            status=status,
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            paused_at=data.get("paused_at"),
            codebase_root=data.get("codebase_root", "."),
            reasoning_session_id=data.get("reasoning_session_id"),
            goal_graph_id=data.get("goal_graph_id"),
            progress=SessionProgress.from_dict(data.get("progress")),
            budget={"spent_usd": float(spent)},
            sandbox=SessionSandbox.from_dict(data.get("sandbox")),
            parent_session_id=data.get("parent_session_id"),
            cancel_requested=bool(data.get("cancel_requested", False)),
            goal_hash=data.get("goal_hash") or goal_hash(goal),
            pause_reason=data.get("pause_reason"),
        )


class SessionResumeError(Exception):
    """Raised when resume preconditions are not met."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def goal_hash(goal: str) -> str:
    """Normalized goal hash (matches AgentStateManager convention)."""
    return hashlib.sha256(goal.strip().lower().encode()).hexdigest()[:10]


def new_session_id() -> str:
    return f"rs_{uuid.uuid4().hex[:12]}"


def validate_session_id(session_id: str) -> None:
    if not SESSION_ID_RE.match(session_id):
        raise ValueError(f"invalid session_id format: {session_id!r}")


class RuntimeSessionStore:
    """CRUD for .awos/sessions/rs_*.json"""

    SESSIONS_DIR = Path(".awos/sessions")

    def __init__(self, sessions_dir: Optional[str] = None) -> None:
        self.sessions_dir = Path(sessions_dir) if sessions_dir else self.SESSIONS_DIR
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def create(self, goal: str, codebase_root: str = ".") -> RuntimeSession:
        goal = goal.strip()
        if not goal:
            raise ValueError("goal must be non-empty")

        now = _now_iso()
        session = RuntimeSession(
            session_id=new_session_id(),
            goal=goal,
            status=SessionStatus.PENDING,
            created_at=now,
            updated_at=now,
            codebase_root=codebase_root,
            goal_hash=goal_hash(goal),
        )
        self.save(session)
        return session

    def load(self, session_id: str) -> RuntimeSession:
        validate_session_id(session_id)
        path = self._path(session_id)
        if not path.exists():
            raise FileNotFoundError(f"session not found: {session_id}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError(f"corrupt session file {session_id}: {exc}") from exc
        return RuntimeSession.from_dict(data)

    def save(self, session: RuntimeSession) -> None:
        validate_session_id(session.session_id)
        session.updated_at = _now_iso()
        if not session.goal_hash:
            session.goal_hash = goal_hash(session.goal)
        path = self._path(session.session_id)
        self._atomic_write(path, session.to_dict())

    def list_sessions(
        self,
        status: Optional[SessionStatus] = None,
        goal: Optional[str] = None,
    ) -> list[RuntimeSession]:
        """Return durable sessions sorted by updated time."""
        sessions: list[RuntimeSession] = []
        for path in self.sessions_dir.glob("rs_*.json"):
            try:
                session = RuntimeSession.from_dict(
                    json.loads(path.read_text(encoding="utf-8"))
                )
            except Exception as exc:
                logger.warning("[RuntimeSession] skipping corrupt file %s: %s", path.name, exc)
                continue
            if status is not None and session.status != status:
                continue
            if goal is not None and session.goal_hash != goal_hash(goal):
                continue
            sessions.append(session)

        def _sort_key(s: RuntimeSession) -> tuple[float, str]:
            path = self.sessions_dir / f"{s.session_id}.json"
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            return (mtime, s.session_id)

        sessions.sort(key=_sort_key, reverse=True)
        return sessions

    def find_latest_for_goal(self, goal: str) -> Optional[RuntimeSession]:
        """Latest paused or running session for a goal (normalized hash)."""
        gh = goal_hash(goal)
        candidates = [
            s
            for s in self.list_sessions()
            if s.goal_hash == gh and s.status in (SessionStatus.PAUSED, SessionStatus.RUNNING)
        ]
        return candidates[0] if candidates else None

    def request_cancel(self, session_id: str) -> RuntimeSession:
        session = self.load(session_id)
        session.cancel_requested = True
        self.save(session)
        return session

    def begin_resume(self, session_id: str) -> RuntimeSession:
        """Load a paused session and mark it running."""
        session = self.load(session_id)
        if session.status != SessionStatus.PAUSED:
            raise SessionResumeError(
                f"cannot resume session {session_id} with status {session.status.value}"
            )
        session.status = SessionStatus.RUNNING
        session.paused_at = None
        session.cancel_requested = False
        self.save(session)
        return session

    def fork(self, session_id: str, new_goal: Optional[str] = None) -> RuntimeSession:
        parent = self.load(session_id)
        now = _now_iso()
        goal = (new_goal or parent.goal).strip()
        if not goal:
            raise ValueError("goal must be non-empty")

        child = RuntimeSession(
            session_id=new_session_id(),
            goal=goal,
            status=SessionStatus.PENDING,
            created_at=now,
            updated_at=now,
            codebase_root=parent.codebase_root,
            goal_graph_id=parent.goal_graph_id,
            progress=deepcopy(parent.progress),
            budget=dict(parent.budget),
            sandbox=SessionSandbox.from_dict(parent.sandbox.to_dict()),
            parent_session_id=parent.session_id,
            goal_hash=goal_hash(goal),
        )
        self.save(child)
        return child

    def checkpoint(
        self,
        session: RuntimeSession,
        *,
        completed_task_id: Any = None,
        failed_task_id: Any = None,
        current_task_id: Any = None,
        total_tasks: Optional[int] = None,
        decomposition_depth: Optional[int] = None,
        spent_usd: Optional[float] = None,
        reasoning_session_id: Optional[str] = None,
        goal_graph_id: Optional[str] = None,
    ) -> RuntimeSession:
        if completed_task_id is not None:
            if completed_task_id not in session.progress.completed_task_ids:
                session.progress.completed_task_ids.append(completed_task_id)
            session.progress.failed_task_ids = [
                t for t in session.progress.failed_task_ids if t != completed_task_id
            ]
        if failed_task_id is not None:
            if failed_task_id not in session.progress.failed_task_ids:
                session.progress.failed_task_ids.append(failed_task_id)
        if current_task_id is not None:
            session.progress.current_task_id = current_task_id
        if total_tasks is not None:
            session.progress.total_tasks = total_tasks
        if decomposition_depth is not None:
            session.progress.decomposition_depth = decomposition_depth
        if spent_usd is not None:
            session.budget["spent_usd"] = float(spent_usd)
        if reasoning_session_id is not None:
            session.reasoning_session_id = reasoning_session_id
        if goal_graph_id is not None:
            session.goal_graph_id = goal_graph_id
        self.save(session)
        return session

    def finalize(self, session: RuntimeSession, status: SessionStatus) -> RuntimeSession:
        terminal = {
            SessionStatus.COMPLETED,
            SessionStatus.FAILED,
            SessionStatus.CANCELLED,
            SessionStatus.PAUSED,
        }
        if status not in terminal:
            raise ValueError(f"finalize expects terminal status, got {status.value}")

        session.status = status
        session.cancel_requested = False
        if status == SessionStatus.PAUSED:
            session.paused_at = _now_iso()
        self.save(session)
        return session

    def _path(self, session_id: str) -> Path:
        validate_session_id(session_id)
        return self.sessions_dir / f"{session_id}.json"

    def _atomic_write(self, path: Path, data: dict[str, Any]) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            os.replace(str(tmp), str(path))
        except OSError as exc:
            logger.warning("[RuntimeSession] atomic write failed for %s: %s", path.name, exc)
            raise
