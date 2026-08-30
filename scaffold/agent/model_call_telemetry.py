"""Best-effort per-call token and context telemetry."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _estimate_tokens(text: str) -> int:
    return max(0, len(text) // 4)


def _value(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def extract_optional_usage(usage: Any) -> dict[str, int | None]:
    """Extract provider-specific optional usage fields without assuming a schema."""
    if usage is None:
        return {"reasoning_tokens": None, "cache_read_tokens": None, "cache_write_tokens": None}
    details = _value(usage, "prompt_tokens_details", {}) or {}
    completion_details = _value(usage, "completion_tokens_details", {}) or {}
    return {
        "reasoning_tokens": _value(completion_details, "reasoning_tokens"),
        "cache_read_tokens": _value(details, "cached_tokens"),
        "cache_write_tokens": _value(usage, "cache_creation_input_tokens"),
    }


def _context_categories(messages: list[dict[str, Any]]) -> dict[str, int]:
    categories = {
        "system_instruction": 0,
        "conversation_history": 0,
        "tool_output": 0,
        "retrieved_project_context": 0,
        "current_task_prompt": 0,
    }
    user_count = 0
    for message in messages:
        text = str(message.get("content", ""))
        estimated = _estimate_tokens(text)
        role = str(message.get("role", ""))
        if role == "system":
            categories["system_instruction"] += estimated
        elif role == "assistant":
            categories["conversation_history"] += estimated
        elif text.startswith("OBSERVATION"):
            categories["tool_output"] += estimated
        elif user_count == 0:
            categories["current_task_prompt"] += estimated
            user_count += 1
            for label in ("ARCHITECTURE:", "REPO MAP:", "EXPLORATION:", "CODE:"):
                if label in text:
                    categories["retrieved_project_context"] += _estimate_tokens(
                        text.split(label, 1)[1].split("\n\n", 1)[0]
                    )
        else:
            categories["conversation_history"] += estimated
    return categories


def _prompt_categories(prompt: str) -> dict[str, int]:
    categories = {
        "system_instruction": 0,
        "conversation_history": 0,
        "tool_output": 0,
        "retrieved_project_context": 0,
        "current_task_prompt": _estimate_tokens(prompt),
    }
    for label in ("ARCHITECTURE:", "REPO MAP:", "EXPLORATION:", "CODEBASE STRUCTURE:", "CURRENT CODE:"):
        if label in prompt:
            categories["retrieved_project_context"] += _estimate_tokens(
                prompt.split(label, 1)[1].split("\n\n", 1)[0]
            )
    return categories


def _count_tool_calls(messages: list[dict[str, Any]]) -> int:
    count = 0
    for message in messages:
        if message.get("role") != "assistant":
            continue
        try:
            payload = json.loads(str(message.get("content", "")))
        except (TypeError, json.JSONDecodeError):
            continue
        if payload.get("action") and payload.get("action") != "finish":
            count += 1
    return count


@dataclass
class ModelCallRecord:
    timestamp: str
    provider: str
    model: str
    request_type: str
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    total_tokens: int
    estimated_cost_usd: float | None
    tool_calls: int
    tool_output_chars: int
    session_id: str | None
    task_id: str | None
    step: int | None
    latency_ms: float
    context: dict[str, int] = field(default_factory=dict)
    usage_source: str = "provider"
    context_estimation: str = "approximate"


class ModelCallTelemetryStore:
    """Append-only JSONL store. Telemetry failures never escape this class."""

    def __init__(self, store_path: str = ".awos") -> None:
        self.path = Path(store_path) / "model_calls.jsonl"
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def record(self, record: ModelCallRecord) -> None:
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(record), sort_keys=True) + "\n")
        except (OSError, TypeError, ValueError):
            pass

    def read(self, limit: int | None = None) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except OSError:
            return []
        return records[-limit:] if limit else records

    def aggregate(self, records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        rows = records if records is not None else self.read()
        totals = {
            "calls": len(rows),
            "input_tokens": sum(int(r.get("input_tokens", 0) or 0) for r in rows),
            "output_tokens": sum(int(r.get("output_tokens", 0) or 0) for r in rows),
            "total_tokens": sum(int(r.get("total_tokens", 0) or 0) for r in rows),
            "estimated_cost_usd": sum(float(r.get("estimated_cost_usd", 0) or 0) for r in rows),
            "tool_calls": sum(int(r.get("tool_calls", 0) or 0) for r in rows),
            "tool_output_chars": sum(int(r.get("tool_output_chars", 0) or 0) for r in rows),
            "latency_ms": sum(float(r.get("latency_ms", 0) or 0) for r in rows),
            "cache_read_tokens": sum(int(r.get("cache_read_tokens", 0) or 0) for r in rows),
            "cache_write_tokens": sum(int(r.get("cache_write_tokens", 0) or 0) for r in rows),
            "reasoning_tokens": sum(int(r.get("reasoning_tokens", 0) or 0) for r in rows),
            "context": {},
        }
        for row in rows:
            for key, value in (row.get("context") or {}).items():
                totals["context"][key] = totals["context"].get(key, 0) + int(value or 0)
        totals["average_latency_ms"] = totals["latency_ms"] / len(rows) if rows else 0.0
        totals["cache_hit_rate"] = (
            totals["cache_read_tokens"] / totals["input_tokens"]
            if totals["input_tokens"] > 0
            else 0.0
        )
        return totals


def record_model_call(
    *,
    provider: str,
    model: str,
    request_type: str,
    input_tokens: int,
    output_tokens: int,
    input_price: float = 0.0,
    output_price: float = 0.0,
    reasoning_tokens: int | None = None,
    cache_read_tokens: int | None = None,
    cache_write_tokens: int | None = None,
    messages: list[dict[str, Any]] | None = None,
    prompt: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    step: int | None = None,
    started_at: float | None = None,
    usage_source: str = "provider",
    store: ModelCallTelemetryStore | None = None,
) -> ModelCallRecord:
    message_list = messages or []
    context = _context_categories(message_list) if messages is not None else _prompt_categories(prompt or "")
    tool_output_chars = sum(
        len(str(m.get("content", "")))
        for m in message_list
        if str(m.get("content", "")).startswith("OBSERVATION")
    )
    cost = (input_tokens / 1_000_000) * input_price + (output_tokens / 1_000_000) * output_price
    record = ModelCallRecord(
        timestamp=datetime.now(timezone.utc).isoformat(),
        provider=provider,
        model=model,
        request_type=request_type,
        input_tokens=int(input_tokens),
        output_tokens=int(output_tokens),
        reasoning_tokens=reasoning_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        total_tokens=int(input_tokens) + int(output_tokens),
        estimated_cost_usd=cost if input_price or output_price else None,
        tool_calls=_count_tool_calls(message_list),
        tool_output_chars=tool_output_chars,
        session_id=session_id,
        task_id=task_id,
        step=step,
        latency_ms=max(0.0, (time.perf_counter() - started_at) * 1000) if started_at else 0.0,
        context=context,
        usage_source=usage_source,
    )
    (store or ModelCallTelemetryStore()).record(record)
    return record
