"""Compact response contract for coding-agent sessions."""
from __future__ import annotations

from typing import Any

STATUSES = {"done", "blocked", "needs-research"}


def validate_record(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    status = record.get("status")
    if status not in STATUSES:
        errors.append(f"status must be one of: {', '.join(sorted(STATUSES))}")
    for field in ("changed", "verified"):
        if not isinstance(record.get(field, []), list):
            errors.append(f"{field} must be a list")
    if not isinstance(record.get("next", ""), str):
        errors.append("next must be a string")
    return errors


def render_record(record: dict[str, Any]) -> str:
    errors = validate_record(record)
    if errors:
        raise ValueError("invalid response record: " + "; ".join(errors))
    lines = [f"STATUS: {record['status']}", "CHANGED:"]
    for item in record["changed"]:
        lines.append(f"- {item.get('path', '?')}:{item.get('line', '?')} — {item.get('summary', '')}")
    lines.append("VERIFIED:")
    for item in record["verified"]:
        lines.append(f"- {item.get('command', '?')} — {item.get('result', '')}")
    lines.append(f"NEXT: {record.get('next', '')}")
    return "\n".join(lines)


__all__ = ["STATUSES", "validate_record", "render_record"]
