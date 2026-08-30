"""Tests for normalization of existing AWOS runtime artifacts."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scaffold.agent.core.debug_adapters import (
    adapt_gui_event,
    adapt_gui_events,
    adapt_session_telemetry,
    adapt_task_span,
    adapt_task_telemetry,
    normalize_event_type,
)
from scaffold.agent.core.debug_schema import EvidenceKind
from scaffold.agent.core.observability import TaskSpan
from scaffold.agent.telemetry_log import SessionTelemetry, TaskTelemetry


def test_task_span_adapter_preserves_operational_facts_and_source():
    span = TaskSpan(
        span_id="span-1",
        task_id="task-1",
        goal="fix parser",
        action="edit file",
        file="src/app.py",
        enqueue_ts=10.0,
        routing_ts=10.1,
        worker_start_ts=10.2,
        complete_ts=11.0,
        model_chosen="model-a",
        escalation_level=2,
        strategy="repair",
        attempt_count=2,
        success=False,
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.01,
    )

    evidence = adapt_task_span(span, source_ref="spans.jsonl:7")

    assert evidence.evidence_id == "span:span-1"
    assert evidence.kind is EvidenceKind.DIRECT_OBSERVATION
    assert evidence.source_ref == "spans.jsonl:7"
    assert evidence.payload["task_id"] == "task-1"
    assert evidence.payload["success"] is False
    assert evidence.payload["total_latency_ms"] == 1000.0


def test_task_telemetry_adapter_preserves_failure_context_without_proof():
    telemetry = TaskTelemetry(
        session_id="session-1",
        task_id="task-1",
        timestamp="2026-07-26T12:00:00Z",
        success=False,
        verify_passed=False,
        failure_stage="verify",
        failure_reason="check failed",
        tests_failed=1,
    )

    evidence = adapt_task_telemetry(telemetry, source_ref="telemetry.jsonl:3")

    assert evidence.source == "telemetry.task"
    assert evidence.source_ref == "telemetry.jsonl:3"
    assert evidence.valid is True
    assert evidence.payload["failure_stage"] == "verify"
    assert evidence.payload["failure_reason"] == "check failed"
    assert evidence.kind is EvidenceKind.DIRECT_OBSERVATION


def test_session_telemetry_adapter_uses_session_source_identity():
    telemetry = SessionTelemetry(
        session_id="session-2",
        timestamp="2026-07-26T12:00:00Z",
        tasks_completed=2,
        tasks_failed=1,
    )

    evidence = adapt_session_telemetry(telemetry)

    assert evidence.evidence_id.startswith("telemetry:session:session-2:")
    assert evidence.event_id == "session-2"
    assert evidence.payload["record_type"] == "session_telemetry"


def test_legacy_and_current_event_names_share_canonical_type_and_stage():
    assert normalize_event_type("planning_done") == "plan_generated"
    assert normalize_event_type("verify_result") == "test_result"
    assert normalize_event_type("file_change") == "file_edit"

    current = adapt_gui_event(
        {
            "type": "test_result",
            "session_id": "session-3",
            "timestamp": "2026-07-26T12:00:00Z",
            "payload": {"passed": False, "output": "traceback"},
        },
        source_ref="events.jsonl:1",
        ordinal=1,
    )
    legacy = adapt_gui_event(
        {
            "type": "verify_result",
            "session_id": "session-3",
            "timestamp": "2026-07-26T12:00:00Z",
            "payload": {"passed": False, "output": "traceback"},
        },
        source_ref="events.jsonl:1",
        ordinal=1,
    )

    assert current.evidence_id == legacy.evidence_id
    assert current.payload["normalized_event_type"] == "test_result"
    assert legacy.payload["normalized_event_type"] == "test_result"
    assert current.payload["normalized_event_type"] == legacy.payload[
        "normalized_event_type"
    ]
    assert current.payload["passed"] == legacy.payload["passed"]
    assert current.valid is True
    assert legacy.valid is True


def test_gui_event_adapter_preserves_unknown_names_and_invalid_timestamp():
    evidence = adapt_gui_event(
        {
            "type": "future_event",
            "session_id": "session-4",
            "timestamp": "not-a-timestamp",
            "payload": {"value": 1},
        },
        source_ref="events.jsonl:9",
    )

    assert evidence.payload["original_event_type"] == "future_event"
    assert evidence.payload["normalized_event_type"] == "future_event"
    assert evidence.payload["timestamp_parse_error"] is True
    assert evidence.observed_at == 0.0
    assert evidence.valid is False


def test_gui_event_stream_preserves_order_and_source_ref():
    events = [
        {
            "type": "session_start",
            "session_id": "session-5",
            "timestamp": "2026-07-26T12:00:00Z",
            "payload": {},
        },
        {
            "type": "file_change",
            "session_id": "session-5",
            "timestamp": "2026-07-26T12:00:01Z",
            "payload": {"path": "src/app.py"},
        },
    ]

    evidence = list(adapt_gui_events(events, source_ref="events.jsonl"))

    assert [item.event_id for item in evidence] == [
        "gui:session-5:1",
        "gui:session-5:2",
    ]
    assert evidence[0].payload["normalized_event_type"] == "session_start"
    assert evidence[1].payload["normalized_event_type"] == "file_edit"
    assert evidence[1].payload["path"] == "src/app.py"
    assert all(item.source_ref == "events.jsonl" for item in evidence)
