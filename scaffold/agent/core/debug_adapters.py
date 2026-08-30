"""Adapters from existing AWOS runtime records to debugger evidence.

Adapters preserve the original source and payload. They normalize vocabulary and
stage labels, but deliberately do not classify findings or infer root causes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional

from ..gui_events import EventType
from ..telemetry_log import SessionTelemetry, TaskTelemetry
from .debug_schema import DebugEvidence, FailureStage, EvidenceKind
from .observability import TaskSpan


# Legacy renderer/event names mapped to the current logical event vocabulary.
_EVENT_ALIASES = {
    "planning_done": EventType.PLAN_GENERATED,
    "worker_start": EventType.TASK_START,
    "worker_done": EventType.TASK_COMPLETE,
    "verify_result": EventType.TEST_RESULT,
    "task_done": EventType.TASK_COMPLETE,
    "retry_notice": "retry_notice",
    "file_change": EventType.FILE_EDIT,
    "session_complete": EventType.SESSION_DONE,
}

_STAGE_BY_EVENT = {
    EventType.SESSION_START: FailureStage.SESSION,
    EventType.CODEBASE_INDEXED: FailureStage.PLANNING,
    EventType.PLAN_GENERATED: FailureStage.PLANNING,
    EventType.MODEL_ROUTED: FailureStage.ROUTING,
    EventType.TASK_START: FailureStage.EXECUTION,
    EventType.AGENT_THINKING: FailureStage.EXECUTION,
    EventType.AGENT_TOOL_CALL: FailureStage.EXECUTION,
    EventType.TASK_COMPLETE: FailureStage.EXECUTION,
    EventType.FILE_EDIT: FailureStage.EDIT,
    EventType.TEST_RESULT: FailureStage.TEST,
    EventType.SESSION_DONE: FailureStage.SESSION,
    "retry_notice": FailureStage.EXECUTION,
}


def normalize_event_type(event_type: str) -> str:
    """Return one canonical event name while preserving unknown names."""
    return _EVENT_ALIASES.get(event_type, event_type)


def _timestamp_to_epoch(value: Any) -> tuple[float, bool]:
    """Parse supported source timestamps without inventing a current time."""
    if isinstance(value, (int, float)):
        return float(value), True
    if not isinstance(value, str) or not value:
        return 0.0, False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp(), True
    except ValueError:
        return 0.0, False


def _failure_stage(value: Any, event_type: str = "") -> FailureStage:
    if isinstance(value, FailureStage):
        return value
    if isinstance(value, str) and value:
        normalized = value.upper().replace("-", "_").replace(" ", "_")
        try:
            return FailureStage(normalized)
        except ValueError:
            pass
    return _STAGE_BY_EVENT.get(event_type, FailureStage.UNKNOWN)


def adapt_task_span(span: TaskSpan, source_ref: Optional[str] = None) -> DebugEvidence:
    """Convert one TaskSpan into source-backed operational evidence."""
    observed_at = span.complete_ts or span.worker_start_ts or span.enqueue_ts
    return DebugEvidence(
        evidence_id=f"span:{span.span_id}",
        kind=EvidenceKind.DIRECT_OBSERVATION,
        source="observability.spans",
        observed_at=observed_at,
        source_ref=source_ref or ".awos/spans.jsonl",
        event_id=span.span_id,
        payload={
            "record_type": "task_span",
            "task_id": span.task_id,
            "goal": span.goal,
            "action": span.action,
            "file": span.file,
            "model_chosen": span.model_chosen,
            "escalation_level": span.escalation_level,
            "strategy": span.strategy,
            "attempt_count": span.attempt_count,
            "success": span.success,
            "input_tokens": span.input_tokens,
            "output_tokens": span.output_tokens,
            "cost_usd": span.cost_usd,
            "queue_wait_ms": span.queue_wait_ms,
            "worker_latency_ms": span.worker_latency_ms,
            "total_latency_ms": span.total_latency_ms,
        },
    )


def adapt_task_telemetry(
    telemetry: TaskTelemetry, source_ref: Optional[str] = None
) -> DebugEvidence:
    """Convert task improvement telemetry without treating outcomes as proof."""
    observed_at, timestamp_valid = _timestamp_to_epoch(telemetry.timestamp)
    payload = telemetry.to_dict()
    payload["record_type"] = "task_telemetry"
    if not timestamp_valid:
        payload["timestamp_parse_error"] = True
    return DebugEvidence(
        evidence_id=(
            f"telemetry:task:{telemetry.session_id}:"
            f"{telemetry.task_id}:{telemetry.timestamp}"
        ),
        kind=EvidenceKind.DIRECT_OBSERVATION,
        source="telemetry.task",
        observed_at=observed_at,
        source_ref=source_ref or ".awos/telemetry.jsonl",
        event_id=telemetry.span_id or telemetry.task_id,
        payload=payload,
        valid=timestamp_valid,
    )


def adapt_session_telemetry(
    telemetry: SessionTelemetry, source_ref: Optional[str] = None
) -> DebugEvidence:
    """Convert session-level telemetry into a separate evidence item."""
    observed_at, timestamp_valid = _timestamp_to_epoch(telemetry.timestamp)
    payload = telemetry.to_dict()
    payload["record_type"] = "session_telemetry"
    if not timestamp_valid:
        payload["timestamp_parse_error"] = True
    return DebugEvidence(
        evidence_id=f"telemetry:session:{telemetry.session_id}:{telemetry.timestamp}",
        kind=EvidenceKind.DIRECT_OBSERVATION,
        source="telemetry.session",
        observed_at=observed_at,
        source_ref=source_ref or ".awos/telemetry.jsonl",
        event_id=telemetry.session_id,
        payload=payload,
        valid=timestamp_valid,
    )


def adapt_gui_event(
    event: Mapping[str, Any],
    source_ref: Optional[str] = None,
    ordinal: Optional[int] = None,
) -> DebugEvidence:
    """Normalize one current or legacy GUI event into evidence."""
    original_type = str(event.get("type", ""))
    normalized_type = normalize_event_type(original_type)
    session_id = str(event.get("session_id", ""))
    timestamp = event.get("timestamp")
    observed_at, timestamp_valid = _timestamp_to_epoch(timestamp)
    event_id = str(event.get("event_id") or "")
    if not event_id:
        suffix = str(ordinal) if ordinal is not None else str(timestamp)
        event_id = f"gui:{session_id}:{suffix}"

    payload = dict(event.get("payload") or {})
    payload["original_event_type"] = original_type
    payload["normalized_event_type"] = normalized_type
    if not timestamp_valid:
        payload["timestamp_parse_error"] = True

    return DebugEvidence(
        evidence_id=event_id,
        kind=EvidenceKind.DIRECT_OBSERVATION,
        source="gui.events",
        observed_at=observed_at,
        source_ref=source_ref,
        event_id=event_id,
        payload=payload,
        valid=timestamp_valid and bool(original_type) and bool(session_id),
    )


def adapt_gui_events(
    events: Iterable[Mapping[str, Any]], source_ref: Optional[str] = None
) -> Iterator[DebugEvidence]:
    """Normalize an ordered event stream while retaining source order."""
    for ordinal, event in enumerate(events, start=1):
        yield adapt_gui_event(event, source_ref=source_ref, ordinal=ordinal)


def read_jsonl(path: Path | str) -> Iterator[Dict[str, Any]]:
    """Read JSON object records while reporting the exact corrupt line."""
    path = Path(path)
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        import json

        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL record at {path}:{line_number}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"JSONL record at {path}:{line_number} must be an object")
        yield value
