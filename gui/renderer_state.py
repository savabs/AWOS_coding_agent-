"""Pure projection of persisted AWOS execution events for the developer renderer."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def _payload(event: Mapping[str, Any]) -> dict[str, Any]:
    value = event.get("payload", {})
    return dict(value) if isinstance(value, Mapping) else {}


def _task(state: dict[str, Any], task_id: Any) -> dict[str, Any]:
    key = str(task_id if task_id is not None else "unknown")
    return state["tasks"].setdefault(
        key,
        {
            "task_id": key,
            "status": "pending",
            "action": "",
            "file": "",
            "model": "",
            "events": [],
        },
    )


def project_session(
    events: Iterable[Mapping[str, Any]],
    manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project raw GUI events and an optional manifest into renderer state.

    Invalid event values are represented as errors in the projection rather than
    raising, so a developer can inspect a damaged or partial session honestly.
    """
    state: dict[str, Any] = {
        "session": {
            "session_id": "",
            "goal": "",
            "status": "idle",
            "codebase_root": "",
            "elapsed": 0.0,
            "total_cost": 0.0,
        },
        "timeline": [],
        "tasks": {},
        "exploration": [],
        "files": {},
        "verification": [],
        "evidence": [],
        "errors": [],
    }

    if isinstance(manifest, Mapping):
        session = state["session"]
        session.update(
            {
                key: manifest[key]
                for key in ("session_id", "goal", "status", "codebase_root", "elapsed_sec", "total_cost_usd")
                if key in manifest
            }
        )
        session["elapsed"] = manifest.get("elapsed", manifest.get("elapsed_sec", 0.0))
        session["total_cost"] = manifest.get("total_cost", manifest.get("total_cost_usd", 0.0))
        for file_change in manifest.get("files", []) if isinstance(manifest.get("files", []), list) else []:
            if isinstance(file_change, Mapping) and file_change.get("path"):
                state["files"][str(file_change["path"])] = dict(file_change)

    try:
        event_list = list(events)
    except TypeError:
        state["errors"].append("Event stream is not iterable")
        return _finalize(state)

    for raw_event in event_list:
        if not isinstance(raw_event, Mapping):
            state["errors"].append("Ignored malformed event")
            continue
        event_type = raw_event.get("type")
        if not isinstance(event_type, str) or not event_type:
            state["errors"].append("Ignored event without a type")
            continue
        payload = _payload(raw_event)
        event = {
            "type": event_type,
            "session_id": raw_event.get("session_id", ""),
            "timestamp": raw_event.get("timestamp", ""),
            "payload": payload,
        }
        state["timeline"].append(event)
        session = state["session"]
        if raw_event.get("session_id") and not session["session_id"]:
            session["session_id"] = raw_event["session_id"]

        if event_type == "session_start":
            session.update(
                {
                    "status": "running",
                    "goal": payload.get("goal", session["goal"]),
                    "codebase_root": payload.get("codebase_root", session["codebase_root"]),
                }
            )
        elif event_type in {"task_start", "task_complete"}:
            item = _task(state, payload.get("task_id"))
            item["events"].append(event_type)
            if event_type == "task_start":
                item.update(
                    {
                        "status": "running",
                        "action": payload.get("action", ""),
                        "file": payload.get("file", ""),
                        "model": payload.get("model_name", ""),
                    }
                )
            else:
                item.update(
                    {
                        "status": "completed" if payload.get("success", True) else "failed",
                        "error": payload.get("error", ""),
                        "cost_usd": payload.get("cost_usd", 0.0),
                    }
                )
        elif event_type in {"agent_tool_call", "tool_call", "exploration"}:
            state["exploration"].append(
                {
                    "action": payload.get("action", payload.get("tool", "")),
                    "input": payload.get("action_input", payload.get("input", {})),
                    "observation": payload.get("observation", ""),
                    "success": payload.get("success", True),
                    "latency_ms": payload.get("latency_ms", 0.0),
                    "turn": payload.get("turn", 0),
                }
            )
        elif event_type in {"file_edit", "file_change"}:
            path = payload.get("path")
            if path:
                state["files"][str(path)] = payload
        elif event_type in {"test_result", "verification", "verification_result"}:
            state["verification"].append(payload)
        elif event_type in {"debug_finding", "evidence", "debug"}:
            state["evidence"].append(payload)
        elif event_type == "error":
            state["errors"].append(payload.get("message", payload.get("error", "Unknown error")))
            session["status"] = "error"
        elif event_type == "session_done":
            session.update(
                {
                    "status": "completed" if not payload.get("failed", 0) else "partial",
                    "elapsed": payload.get("elapsed", session["elapsed"]),
                    "total_cost": payload.get("total_cost", session["total_cost"]),
                }
            )

    return _finalize(state)


def _finalize(state: dict[str, Any]) -> dict[str, Any]:
    state["tasks"] = list(state["tasks"].values())
    state["files"] = list(state["files"].values())
    state["parallel_task_count"] = len(state["tasks"])
    if state["errors"] and state["session"]["status"] == "idle":
        state["session"]["status"] = "error"
    return state
