"""Durable, provider-free contracts for one AWOS coding run.

This module owns the run manifest, lifecycle events, transition validation,
replay checks, and completion evidence contract. It deliberately does not
perform workspace or model side effects.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1
_REDACTED = "[REDACTED]"
_SECRET_KEY = re.compile(r"(?:api[_-]?key|authorization|bearer|credential|password|secret|token)", re.I)


class RunState(str, Enum):
    ADMITTED = "admitted"
    PROVISIONING = "provisioning"
    EXPLORING = "exploring"
    PLANNING = "planning"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    AWAITING_REVIEW = "awaiting_review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class RunStateTransitionError(ValueError):
    """Raised when a run lifecycle transition is invalid."""


def _redact(value: Any, key: str = "") -> Any:
    if _SECRET_KEY.search(key):
        return _REDACTED
    if isinstance(value, dict):
        return {str(k): _redact(item, str(k)) for k, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, key) for item in value]
    if isinstance(value, tuple):
        return [_redact(item, key) for item in value]
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class RunManifest:
    """Immutable inputs and policy identity for one autonomous run."""

    run_id: str
    goal: str
    repository: str
    commit: str
    policy: dict[str, Any] = field(default_factory=dict)
    model: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, Any] = field(default_factory=dict)
    workspace: dict[str, Any] = field(default_factory=dict)
    tool_profile: str = "coding"
    seed: int | None = None
    created_at: str = field(default_factory=_now)
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported run manifest schema version: {self.schema_version!r}")
        for field_name in ("run_id", "goal", "repository", "commit", "tool_profile"):
            if not isinstance(getattr(self, field_name), str) or not getattr(self, field_name).strip():
                raise ValueError(f"run manifest {field_name} must be non-empty")
        for field_name in ("policy", "model", "budget", "workspace"):
            if not isinstance(getattr(self, field_name), dict):
                raise ValueError(f"run manifest {field_name} must be an object")

    def to_dict(self) -> dict[str, Any]:
        return _redact({
            "budget": self.budget,
            "commit": self.commit,
            "created_at": self.created_at,
            "goal": self.goal,
            "model": self.model,
            "policy": self.policy,
            "repository": self.repository,
            "run_id": self.run_id,
            "schema_version": self.schema_version,
            "seed": self.seed,
            "tool_profile": self.tool_profile,
            "workspace": self.workspace,
        })

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    def digest(self) -> str:
        return _digest(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunManifest":
        if not isinstance(data, dict):
            raise ValueError("run manifest must be an object")
        expected = {
            "budget", "commit", "created_at", "goal", "model", "policy",
            "repository", "run_id", "schema_version", "seed", "tool_profile", "workspace",
        }
        if set(data) != expected:
            raise ValueError(
                f"run manifest schema mismatch; unknown={sorted(set(data) - expected)}, "
                f"missing={sorted(expected - set(data))}"
            )
        return cls(**data)


@dataclass(frozen=True)
class RunEvent:
    """One hash-addressed append-only lifecycle event."""

    run_id: str
    sequence: int
    event_type: str
    state: RunState
    payload: dict[str, Any]
    event_id: str = field(default_factory=lambda: "evt_" + uuid.uuid4().hex)
    emitted_at: str = field(default_factory=_now)
    previous_digest: str | None = None
    digest: str = ""

    def __post_init__(self) -> None:
        if not self.run_id or self.sequence < 0 or not self.event_type:
            raise ValueError("run event requires run_id, non-negative sequence, and event_type")
        object.__setattr__(self, "state", RunState(self.state))
        if not isinstance(self.payload, dict):
            raise ValueError("run event payload must be an object")
        expected = self.compute_digest()
        if self.digest and self.digest != expected:
            raise ValueError("run event digest mismatch")
        object.__setattr__(self, "digest", expected)

    def _unsigned(self) -> dict[str, Any]:
        return {
            "emitted_at": self.emitted_at,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "payload": _redact(self.payload),
            "previous_digest": self.previous_digest,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "state": self.state.value,
        }

    def compute_digest(self) -> str:
        return _digest(self._unsigned())

    def to_dict(self) -> dict[str, Any]:
        return {**self._unsigned(), "digest": self.digest}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunEvent":
        if not isinstance(data, dict):
            raise ValueError("run event must be an object")
        expected = {
            "emitted_at", "event_id", "event_type", "payload", "previous_digest",
            "run_id", "sequence", "state", "digest",
        }
        if set(data) != expected:
            raise ValueError("run event schema mismatch")
        return cls(**data)


_ALLOWED_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.ADMITTED: frozenset({RunState.PROVISIONING, RunState.CANCELLED, RunState.EXPIRED}),
    RunState.PROVISIONING: frozenset({RunState.EXPLORING, RunState.FAILED, RunState.CANCELLED}),
    RunState.EXPLORING: frozenset({RunState.PLANNING, RunState.FAILED, RunState.CANCELLED}),
    RunState.PLANNING: frozenset({RunState.EXECUTING, RunState.FAILED, RunState.CANCELLED}),
    RunState.EXECUTING: frozenset({RunState.VERIFYING, RunState.FAILED, RunState.CANCELLED}),
    RunState.VERIFYING: frozenset({RunState.AWAITING_REVIEW, RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}),
    RunState.AWAITING_REVIEW: frozenset({RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}),
    RunState.COMPLETED: frozenset(),
    RunState.FAILED: frozenset(),
    RunState.CANCELLED: frozenset(),
    RunState.EXPIRED: frozenset(),
}


class RunStateMachine:
    """In-memory transition authority whose events can be persisted/replayed."""

    def __init__(self, manifest: RunManifest, events: Iterable[RunEvent] | None = None) -> None:
        self.manifest = manifest
        self.state = RunState.ADMITTED
        self.events: list[RunEvent] = []
        if events is None:
            self.events.append(self._event("run_started", {"manifest_digest": manifest.digest()}))
        else:
            self._load(events)

    def _event(self, event_type: str, payload: dict[str, Any]) -> RunEvent:
        sequence = len(self.events)
        previous = self.events[-1].digest if self.events else None
        return RunEvent(
            run_id=self.manifest.run_id,
            sequence=sequence,
            event_type=event_type,
            state=self.state,
            payload=payload,
            previous_digest=previous,
        )

    def transition(self, state: RunState, *, reason: str = "") -> RunEvent:
        state = RunState(state)
        if state not in _ALLOWED_TRANSITIONS[self.state]:
            raise RunStateTransitionError(
                f"cannot transition run from {self.state.value} to {state.value}"
            )
        previous = self.state
        self.state = state
        event = self._event("state_changed", {"from": previous.value, "reason": reason, "to": state.value})
        self.events.append(event)
        return event

    def _load(self, events: Iterable[RunEvent]) -> None:
        replayed = replay_run_events(self.manifest, list(events))
        self.state = replayed.state
        self.events = list(replayed.events)


class RunEventLog:
    """Durable JSONL event log with contiguous sequence and hash-chain checks."""

    def __init__(self, path: str | Path, run_id: str) -> None:
        self.path = Path(path)
        self.run_id = run_id
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def read(self) -> list[RunEvent]:
        if not self.path.exists():
            return []
        events: list[RunEvent] = []
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            try:
                event = RunEvent.from_dict(json.loads(line))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"invalid run event at {self.path}:{line_number}: {exc}"
                ) from exc
            events.append(event)
        self._validate(events)
        return events

    def append(self, event: RunEvent) -> None:
        events = self.read()
        if event.run_id != self.run_id:
            raise ValueError("run event belongs to a different run")
        if event.sequence != len(events):
            raise ValueError("run event sequence must be contiguous")
        if events and event.previous_digest != events[-1].digest:
            raise ValueError("run event previous digest mismatch")
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(_canonical_json(event.to_dict()) + "\n")
            handle.flush()

    def _validate(self, events: list[RunEvent]) -> None:
        previous: str | None = None
        for sequence, event in enumerate(events):
            if event.run_id != self.run_id or event.sequence != sequence:
                raise ValueError("run event sequence or run ID mismatch")
            if event.previous_digest != previous:
                raise ValueError("run event hash chain mismatch")
            previous = event.digest


@dataclass(frozen=True)
class ReplayResult:
    state: RunState
    events: tuple[RunEvent, ...]
    event_count: int


def replay_run_events(manifest: RunManifest, events: Iterable[RunEvent]) -> ReplayResult:
    """Reconstruct lifecycle state and reject tampered or invalid event streams."""
    event_list = list(events)
    if not event_list:
        raise ValueError("run event stream is empty")
    previous: str | None = None
    state = RunState.ADMITTED
    for sequence, event in enumerate(event_list):
        if event.run_id != manifest.run_id or event.sequence != sequence:
            raise ValueError("run event sequence or run ID mismatch")
        if event.previous_digest != previous or event.digest != event.compute_digest():
            raise ValueError("run event digest or hash chain mismatch")
        if sequence == 0:
            if event.event_type != "run_started" or event.state != RunState.ADMITTED:
                raise ValueError("run event stream must start with run_started")
            if event.payload.get("manifest_digest") != manifest.digest():
                raise ValueError("run_started manifest digest mismatch")
        else:
            if event.event_type != "state_changed":
                raise ValueError("only state_changed events may follow run_started")
            target = event.state
            if target not in _ALLOWED_TRANSITIONS[state]:
                raise ValueError(f"invalid replay transition from {state.value} to {target.value}")
            if event.payload.get("from") != state.value or event.payload.get("to") != target.value:
                raise ValueError("state transition event payload mismatch")
            state = target
        previous = event.digest
    return ReplayResult(state=state, events=tuple(event_list), event_count=len(event_list))


class RunContractStore:
    """Persist one manifest and its append-only lifecycle event stream."""

    def __init__(self, root: str | Path = ".awos/runs") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _directory(self, run_id: str) -> Path:
        if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
            raise ValueError("run_id must be a safe path component")
        return self.root / run_id

    def create(self, manifest: RunManifest) -> None:
        directory = self._directory(manifest.run_id)
        directory.mkdir(parents=True, exist_ok=True)
        manifest_path = directory / "manifest.json"
        if manifest_path.exists():
            existing = RunManifest.from_dict(json.loads(manifest_path.read_text(encoding="utf-8")))
            if existing.to_json() != manifest.to_json():
                raise ValueError("run manifest already exists with different contents")
            return
        manifest_path.write_text(manifest.to_json() + "\n", encoding="utf-8")
        RunEventLog(directory / "events.jsonl", manifest.run_id).append(
            RunStateMachine(manifest).events[0]
        )

    def load_manifest(self, run_id: str) -> RunManifest:
        path = self._directory(run_id) / "manifest.json"
        if not path.exists():
            raise FileNotFoundError(f"run manifest not found: {run_id}")
        return RunManifest.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def _log(self, run_id: str) -> RunEventLog:
        return RunEventLog(self._directory(run_id) / "events.jsonl", run_id)

    def transition(self, run_id: str, state: RunState, *, reason: str = "") -> RunEvent:
        manifest = self.load_manifest(run_id)
        event_log = self._log(run_id)
        machine = RunStateMachine(manifest, event_log.read())
        event = machine.transition(state, reason=reason)
        event_log.append(event)
        return event

    def replay(self, run_id: str) -> ReplayResult:
        manifest = self.load_manifest(run_id)
        return replay_run_events(manifest, self._log(run_id).read())


@dataclass
class ResultBundle:
    """Auditable result that cannot complete without acceptance verification."""

    run_id: str
    verifications: list[dict[str, Any]] = field(default_factory=list)
    status: str = "pending"
    warnings: list[str] = field(default_factory=list)

    @property
    def can_complete(self) -> bool:
        return any(
            item.get("gate") == "acceptance" and item.get("status") == "PASS"
            for item in self.verifications
        )

    def add_verification(self, gate: str, status: str, evidence: dict[str, Any]) -> None:
        if not gate or status not in {"PASS", "FAIL", "BLOCKED", "NOT_RUN"}:
            raise ValueError("verification requires a gate and valid status")
        self.verifications.append({"gate": gate, "status": status, "evidence": _redact(evidence)})

    def finalize(self, status: str) -> None:
        if status == "completed" and not self.can_complete:
            raise ValueError("completed result requires a passing acceptance verification")
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError(f"invalid result status: {status!r}")
        self.status = status

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "schema_version": SCHEMA_VERSION,
            "status": self.status,
            "verifications": _redact(self.verifications),
            "warnings": list(self.warnings),
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())
