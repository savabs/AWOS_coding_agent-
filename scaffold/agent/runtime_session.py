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


class SandboxPolicyError(ValueError):
    """Raised when a sandbox policy is unknown, unsafe, or unsupported."""


class SandboxIsolationLevel(str, Enum):
    WORKTREE_ONLY = "worktree_only"
    PROCESS_ISOLATED = "process_isolated"
    CONTAINER_ISOLATED = "container_isolated"


class SandboxNetworkMode(str, Enum):
    DENY = "deny"
    ALLOW_DECLARED = "allow_declared"


class SandboxApprovalMode(str, Enum):
    RISKY = "risky"
    ALWAYS = "always"


@dataclass(frozen=True)
class SandboxPolicy:
    """Explicit, serializable execution policy with conservative defaults.

    This schema describes the guarantee the executor must provide; it does not
    create isolation by itself.  Unsupported guarantees are rejected rather
    than silently downgraded.
    """

    policy_version: str = "sandbox_v1"
    isolation_level: SandboxIsolationLevel | str = SandboxIsolationLevel.WORKTREE_ONLY
    network_mode: SandboxNetworkMode | str = SandboxNetworkMode.DENY
    writable_roots: tuple[str, ...] = ("workspace",)
    command_timeout_seconds: int = 30
    max_output_bytes: int = 1_048_576
    approval_mode: SandboxApprovalMode | str = SandboxApprovalMode.RISKY

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "isolation_level", SandboxIsolationLevel(self.isolation_level))
            object.__setattr__(self, "network_mode", SandboxNetworkMode(self.network_mode))
            object.__setattr__(self, "approval_mode", SandboxApprovalMode(self.approval_mode))
        except (TypeError, ValueError) as exc:
            raise SandboxPolicyError(f"unknown sandbox policy value: {exc}") from exc

        if self.policy_version != "sandbox_v1":
            raise SandboxPolicyError(f"unsupported sandbox policy version: {self.policy_version!r}")
        if self.isolation_level == SandboxIsolationLevel.CONTAINER_ISOLATED:
            raise SandboxPolicyError(
                "container_isolated is unavailable: no container backend is active"
            )
        if not self.writable_roots:
            raise SandboxPolicyError("writable_roots must contain at least one root")
        if any(not isinstance(root, str) or not root.strip() for root in self.writable_roots):
            raise SandboxPolicyError("writable_roots must contain non-empty strings")
        for root in self.writable_roots:
            normalized = root.replace("\\", "/")
            if normalized.startswith("/") or normalized == ".." or normalized.startswith("../"):
                raise SandboxPolicyError(f"writable root escapes workspace: {root!r}")
        if isinstance(self.command_timeout_seconds, bool) or self.command_timeout_seconds <= 0:
            raise SandboxPolicyError("command_timeout_seconds must be positive")
        if isinstance(self.max_output_bytes, bool) or self.max_output_bytes <= 0:
            raise SandboxPolicyError("max_output_bytes must be positive")

    def to_dict(self) -> dict[str, object]:
        approval_mode = SandboxApprovalMode(self.approval_mode)
        isolation_level = SandboxIsolationLevel(self.isolation_level)
        network_mode = SandboxNetworkMode(self.network_mode)
        return {
            "approval_mode": approval_mode.value,
            "command_timeout_seconds": self.command_timeout_seconds,
            "isolation_level": isolation_level.value,
            "max_output_bytes": self.max_output_bytes,
            "network_mode": network_mode.value,
            "policy_version": self.policy_version,
            "writable_roots": list(self.writable_roots),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    def digest(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SandboxPolicy":
        if not isinstance(data, dict):
            raise SandboxPolicyError("sandbox policy must be an object")
        expected = {
            "approval_mode",
            "command_timeout_seconds",
            "isolation_level",
            "max_output_bytes",
            "network_mode",
            "policy_version",
            "writable_roots",
        }
        unknown = set(data) - expected
        missing = expected - set(data)
        if unknown or missing:
            raise SandboxPolicyError(
                f"sandbox policy schema mismatch; unknown={sorted(unknown)}, missing={sorted(missing)}"
            )
        roots = data["writable_roots"]
        if not isinstance(roots, (list, tuple)):
            raise SandboxPolicyError("writable_roots must be a list")
        return cls(
            policy_version=data["policy_version"],
            isolation_level=data["isolation_level"],
            network_mode=data["network_mode"],
            writable_roots=tuple(roots),
            command_timeout_seconds=data["command_timeout_seconds"],
            max_output_bytes=data["max_output_bytes"],
            approval_mode=data["approval_mode"],
        )


class SandboxPathError(ValueError):
    """Raised when a requested path cannot be proven inside the workspace."""


def resolve_workspace_path(workspace_root: str | Path, requested_path: str | Path) -> Path:
    """Resolve a workspace-relative path without permitting filesystem escape."""
    root = Path(workspace_root).resolve(strict=True)
    requested = Path(requested_path)
    requested_text = str(requested_path)
    normalized = requested_text.replace("\\", "/")

    if "\x00" in requested_text:
        raise SandboxPathError("path contains NUL")
    if requested.is_absolute() or normalized.startswith("/"):
        raise SandboxPathError(f"absolute path is not allowed: {requested_path!r}")
    parts = tuple(part for part in normalized.split("/") if part not in ("", "."))
    if ".." in parts:
        raise SandboxPathError(f"path traversal is not allowed: {requested_path!r}")

    candidate = (root / Path(*parts)).resolve(strict=False)
    if candidate != root and root not in candidate.parents:
        raise SandboxPathError(f"path escapes workspace: {requested_path!r}")
    return candidate


@dataclass(frozen=True)
class SandboxSecurityEvidence:
    """Append-only audit record for one sandbox action attempt."""

    evidence_id: str = ""
    session_id: str = ""
    policy_version: str = ""
    policy_digest: str = ""
    isolation_level: str = ""
    action_digest: str = ""
    approval_status: str = ""
    command: str = ""
    arguments: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    environment_identity: dict[str, str] = field(default_factory=dict)
    result_status: str = "unknown"
    exit_status: Optional[int] = None
    timed_out: bool = False
    output_digest: Optional[str] = None
    cleanup_state: str = "unknown"
    observed_at: str = ""

    def __post_init__(self) -> None:
        required = {
            "evidence_id": self.evidence_id,
            "session_id": self.session_id,
            "policy_version": self.policy_version,
            "policy_digest": self.policy_digest,
            "isolation_level": self.isolation_level,
            "action_digest": self.action_digest,
            "command": self.command,
            "observed_at": self.observed_at,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"sandbox evidence missing required fields: {', '.join(missing)}")
        if self.approval_status not in {"approved", "denied", "not_required"}:
            raise ValueError(f"unknown approval status: {self.approval_status!r}")
        if self.result_status not in {"success", "failed", "timed_out", "unknown"}:
            raise ValueError(f"unknown result status: {self.result_status!r}")
        if self.cleanup_state not in {"active", "cleaned", "cleanup_failed", "unknown"}:
            raise ValueError(f"unknown cleanup state: {self.cleanup_state!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_digest": self.action_digest,
            "approval_status": self.approval_status,
            "arguments": list(self.arguments),
            "cleanup_state": self.cleanup_state,
            "command": self.command,
            "environment_identity": dict(self.environment_identity),
            "evidence_id": self.evidence_id,
            "exit_status": self.exit_status,
            "isolation_level": self.isolation_level,
            "observed_at": self.observed_at,
            "output_digest": self.output_digest,
            "paths": list(self.paths),
            "policy_digest": self.policy_digest,
            "policy_version": self.policy_version,
            "result_status": self.result_status,
            "session_id": self.session_id,
            "timed_out": self.timed_out,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SandboxSecurityEvidence":
        expected = set(cls.__dataclass_fields__)
        if set(data) != expected:
            raise ValueError("sandbox evidence schema mismatch")
        values = dict(data)
        values["arguments"] = tuple(values["arguments"])
        values["paths"] = tuple(values["paths"])
        return cls(**values)


class SandboxEvidenceStore:
    """Durable append-only JSONL store for sandbox security evidence."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, evidence: SandboxSecurityEvidence) -> None:
        line = evidence.to_json() + "\n"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def read(self) -> list[SandboxSecurityEvidence]:
        if not self.path.exists():
            return []
        records = []
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                records.append(SandboxSecurityEvidence.from_dict(json.loads(line)))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise ValueError(f"invalid sandbox evidence at {self.path}:{line_number}") from exc
        return records


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
    isolation_level: str = "worktree_only"
    policy_version: Optional[str] = None
    policy_digest: Optional[str] = None
    cleanup_state: str = "not_created"

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
            isolation_level=data.get("isolation_level", "worktree_only"),
            policy_version=data.get("policy_version"),
            policy_digest=data.get("policy_digest"),
            cleanup_state=data.get("cleanup_state", "not_created"),
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
    execution_graph: Optional[dict[str, Any]] = None

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
            "execution_graph": self.execution_graph,
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
        execution_graph = data.get("execution_graph")
        if execution_graph is not None and not isinstance(execution_graph, dict):
            raise ValueError("execution_graph must be an object or null")
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
            execution_graph=execution_graph,
        )


class SessionResumeError(Exception):
    """Raised when resume preconditions are not met."""


class _SessionLease:
    """Process-exclusive admission lease for a session transition."""

    def __init__(self, path: Path, session_id: str) -> None:
        self.path = path
        self.session_id = session_id
        self._held = False

    def acquire(self) -> None:
        payload = json.dumps({"pid": os.getpid(), "session_id": self.session_id})
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            if self._owner_is_dead():
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                return self.acquire()
            raise SessionResumeError(
                f"session {self.session_id} is already being resumed"
            ) from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            self.path.unlink(missing_ok=True)
            raise
        self._held = True

    def release(self) -> None:
        if self._held:
            self.path.unlink(missing_ok=True)
            self._held = False

    def _owner_is_dead(self) -> bool:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            pid = data["pid"]
            if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
                return False
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except (OSError, PermissionError):
            return False
        return False

    def __enter__(self) -> "_SessionLease":
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.release()


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
        with _SessionLease(self._lease_path(session_id), session_id):
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

    def _lease_path(self, session_id: str) -> Path:
        validate_session_id(session_id)
        return self.sessions_dir / f"{session_id}.lease"

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
