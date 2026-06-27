"""
telemetry_log.py — Backend-only improvement telemetry.

Rich execution stats persisted to .awos/telemetry.jsonl for agent learning
and analysis. Not shown in the terminal UI — consumed offline or by analytics.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional


_DEFAULT_PATH = Path(".awos") / "telemetry.jsonl"


@dataclass
class TaskTelemetry:
    """One task's worth of improvement-oriented stats."""

    event: str = "task_complete"
    session_id: str = ""
    task_id: str = ""
    timestamp: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )
    goal: str = ""
    action: str = ""
    file: str = ""
    task_type: str = ""
    success: bool = False

    # Routing / model
    model: str = ""
    strategy: str = ""
    escalation_level: int = 0
    attempt_count: int = 1
    retry_count: int = 0

    # Tokens / cost
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0

    # Verify / edits
    verify_passed: bool = False
    verify_attempts: int = 0
    fidelity_fail: bool = False
    edits_applied: int = 0
    edits_failed: int = 0
    edit_status: str = ""

    # Diff stats
    lines_added: int = 0
    lines_removed: int = 0
    file_status: str = ""

    # Timing (ms)
    queue_wait_ms: float = 0.0
    worker_latency_ms: float = 0.0
    total_latency_ms: float = 0.0

    # Tests
    tests_passed: int = 0
    tests_failed: int = 0
    test_pass_rate: float = 0.0
    no_tests_found: bool = False

    # Failure
    failure_stage: str = ""
    failure_reason: str = ""

    # Learning signals
    reward: float = 0.0
    span_id: str = ""

    # Stage trace (plan → route → worker → verify → test)
    stages: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SessionTelemetry:
    event: str = "session_complete"
    session_id: str = ""
    timestamp: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )
    goal: str = ""
    tasks_completed: int = 0
    tasks_failed: int = 0
    total_cost_usd: float = 0.0
    elapsed_sec: float = 0.0
    files_touched: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    cheap_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TelemetryLogger:
    """Append-only JSONL logger for agent improvement analytics."""

    def __init__(self, path: Optional[Path] = None):
        self._path = path or _DEFAULT_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, default=str)
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def log_task(self, telemetry: TaskTelemetry) -> None:
        self.log(telemetry.to_dict())

    def log_session(self, telemetry: SessionTelemetry) -> None:
        self.log(telemetry.to_dict())

    def path(self) -> Path:
        return self._path
