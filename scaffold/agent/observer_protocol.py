"""Versioned, validated event envelopes for the AWOS observer boundary.

This module deliberately contains only the event contract.  Delivery, replay,
and state projection belong to the observer transports and must not redefine
what an AWOS event is.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, NoReturn

SCHEMA_VERSION = 1
MAX_EVENT_ID_LENGTH = 128
MAX_IDENTIFIER_LENGTH = 256
MAX_EVENT_TYPE_LENGTH = 80
MAX_PAYLOAD_BYTES = 32 * 1024
MAX_PAYLOAD_DEPTH = 8
MAX_PAYLOAD_ITEMS = 256

EVENT_SOURCES = frozenset({"ui", "agent", "kernel", "provider", "observer"})
VISIBILITY_CLASSES = frozenset({"assistant_safe", "developer_only", "redacted"})
_EVENT_ID_RE = re.compile(r"^evt_[A-Za-z0-9_-]+$")
_EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")


class EventEnvelopeError(ValueError):
    """Raised when a value cannot be used as an observer event envelope."""


def _fail(message: str) -> NoReturn:
    raise EventEnvelopeError(message)


def _require_identifier(name: str, value: Any, *, prefix: str | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{name} must be a non-empty string")
    if len(value) > MAX_IDENTIFIER_LENGTH:
        _fail(f"{name} exceeds {MAX_IDENTIFIER_LENGTH} characters")
    if prefix is not None and not value.startswith(prefix):
        _fail(f"{name} must start with {prefix!r}")
    return value


def _validate_payload_value(value: Any, *, depth: int = 0) -> None:
    if depth > MAX_PAYLOAD_DEPTH:
        _fail(f"payload exceeds maximum nesting depth of {MAX_PAYLOAD_DEPTH}")
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, str) and len(value) > MAX_PAYLOAD_BYTES:
            _fail("payload string exceeds the event size limit")
        return
    if isinstance(value, list):
        if len(value) > MAX_PAYLOAD_ITEMS:
            _fail(f"payload list exceeds {MAX_PAYLOAD_ITEMS} items")
        for item in value:
            _validate_payload_value(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > MAX_PAYLOAD_ITEMS:
            _fail(f"payload object exceeds {MAX_PAYLOAD_ITEMS} fields")
        for key, item in value.items():
            if not isinstance(key, str):
                _fail("payload object keys must be strings")
            if len(key) > MAX_IDENTIFIER_LENGTH:
                _fail("payload object key is too long")
            _validate_payload_value(item, depth=depth + 1)
        return
    _fail(f"payload contains unsupported value type: {type(value).__name__}")


def _validate_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value:
        _fail("emitted_at must be a non-empty ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail("emitted_at must be a valid ISO-8601 timestamp")
    if parsed.tzinfo is None:
        _fail("emitted_at must include a timezone")
    return value


def validate_event_envelope(event: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return a JSON-safe observer envelope.

    Validation is intentionally strict at the boundary.  Callers that read a
    durable stream should catch :class:`EventEnvelopeError` and report a
    bounded observer error instead of allowing one malformed line to terminate
    the stream.
    """
    if not isinstance(event, Mapping):
        _fail("event envelope must be an object")

    required = (
        "schema_version",
        "event_id",
        "sequence",
        "emitted_at",
        "source",
        "type",
        "session_id",
        "payload",
        "visibility",
    )
    missing = [key for key in required if key not in event]
    if missing:
        _fail(f"event envelope is missing required fields: {', '.join(missing)}")

    if event["schema_version"] != SCHEMA_VERSION:
        _fail(f"unsupported schema_version: {event['schema_version']!r}")
    _require_identifier("event_id", event["event_id"], prefix="evt_")
    if not _EVENT_ID_RE.fullmatch(event["event_id"]):
        _fail("event_id contains invalid characters")

    sequence = event["sequence"]
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        _fail("sequence must be a non-negative integer")

    _validate_timestamp(event["emitted_at"])
    if event["source"] not in EVENT_SOURCES:
        _fail(f"source must be one of: {', '.join(sorted(EVENT_SOURCES))}")
    if not isinstance(event["type"], str) or not _EVENT_TYPE_RE.fullmatch(event["type"]):
        _fail("type must be a lowercase snake_case event name")
    _require_identifier("session_id", event["session_id"])
    if event["visibility"] not in VISIBILITY_CLASSES:
        _fail(f"visibility must be one of: {', '.join(sorted(VISIBILITY_CLASSES))}")

    for field in ("chat_id", "task_id", "trace_id", "span_id"):
        if field in event and event[field] is not None:
            _require_identifier(field, event[field])

    payload = event["payload"]
    if not isinstance(payload, dict):
        _fail("payload must be an object")
    _validate_payload_value(payload)
    try:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        _fail(f"payload is not JSON serializable: {exc}")
    if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        _fail(f"payload exceeds {MAX_PAYLOAD_BYTES} bytes")

    result = dict(event)
    # A JSON round-trip makes the returned value safe to persist and prevents
    # callers from mutating a nested object after validation.
    result["payload"] = json.loads(encoded)
    return result


def new_event_envelope(
    *,
    event_type: str,
    session_id: str,
    payload: Mapping[str, Any] | None = None,
    sequence: int = 0,
    source: str = "agent",
    visibility: str = "assistant_safe",
    event_id: str | None = None,
    emitted_at: str | None = None,
    chat_id: str | None = None,
    task_id: str | None = None,
    trace_id: str | None = None,
    span_id: str | None = None,
) -> dict[str, Any]:
    """Build and validate one observer event envelope."""
    now = emitted_at or datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    event: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id or f"evt_{uuid.uuid4().hex}",
        "sequence": sequence,
        "emitted_at": now,
        "source": source,
        "type": event_type,
        "session_id": session_id,
        "payload": dict(payload or {}),
        "visibility": visibility,
    }
    for name, value in (
        ("chat_id", chat_id),
        ("task_id", task_id),
        ("trace_id", trace_id),
        ("span_id", span_id),
    ):
        if value is not None:
            event[name] = value
    return validate_event_envelope(event)


def parse_event_line(raw_line: str) -> dict[str, Any] | None:
    """Parse one JSONL line, returning ``None`` for malformed input.

    Durable observer readers use this helper so a truncated or legacy line is
    isolated rather than taking down a live listener.
    """
    try:
        value = json.loads(raw_line)
        return validate_event_envelope(value)
    except (json.JSONDecodeError, EventEnvelopeError, TypeError, ValueError):
        return None


ASSISTANT_MAX_PAYLOAD_BYTES = 8 * 1024
ASSISTANT_SAFE_EVENT_TYPES = frozenset(
    {
        "app_opened",
        "approval",
        "chat_response_completed",
        "chat_response_started",
        "chat_submitted",
        "control_requested",
        "control_completed",
        "control_rejected",
        "codebase_indexed",
        "error",
        "file_change",
        "file_edit",
        "idle",
        "model_routed",
        "plan_generated",
        "session_done",
        "session_selected",
        "session_start",
        "task_complete",
        "task_control",
        "task_start",
        "test_result",
        "view_changed",
        "agent_tool_call",
    }
)
_SECRET_KEY_RE = re.compile(
    r"(?:api[_-]?key|access[_-]?token|auth(?:orization)?|cookie|credential|password|private[_-]?key|secret|token)",
    re.IGNORECASE,
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|authorization|password|private[_-]?key|secret|token)\b\s*[:=]\s*)[^\s,;]+"
)
_BEARER_RE = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]+")


def redact_sensitive_payload(value: Any) -> Any:
    """Recursively redact common credential fields and inline secret values."""
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _SECRET_KEY_RE.search(key) else redact_sensitive_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_payload(item) for item in value]
    if isinstance(value, str):
        value = _SECRET_ASSIGNMENT_RE.sub(r"\1[REDACTED]", value)
        return _BEARER_RE.sub(r"\1[REDACTED]", value)
    return value


def project_assistant_event(event: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the assistant-safe projection, or ``None`` if it is not exposable."""
    validated = validate_event_envelope(event)
    if validated["visibility"] == "developer_only":
        return None
    if validated["type"] not in ASSISTANT_SAFE_EVENT_TYPES:
        return None

    projected = dict(validated)
    payload = redact_sensitive_payload(validated["payload"])
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > ASSISTANT_MAX_PAYLOAD_BYTES:
        payload = {
            "omitted": True,
            "reason": "payload_too_large",
            "payload_bytes": len(encoded.encode("utf-8")),
        }
        projected["visibility"] = "redacted"
    elif payload != validated["payload"]:
        projected["visibility"] = "redacted"
    projected["payload"] = payload
    return projected


def project_assistant_events(events: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Project a mixed/possibly-corrupt event collection without aborting."""
    projected: list[dict[str, Any]] = []
    for event in events:
        try:
            safe_event = project_assistant_event(event)
        except (EventEnvelopeError, TypeError, ValueError):
            continue
        if safe_event is not None:
            projected.append(safe_event)
    return projected


# Short aliases for callers that prefer the noun used in the specification.
validate_envelope = validate_event_envelope
build_event = new_event_envelope
