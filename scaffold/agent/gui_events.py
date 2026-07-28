"""
gui_events.py — Structured events for the AWOS GUI.

LiveRenderer and the orchestrator emit events here. Events are persisted to
.awos/gui/<session_id>/ for the web UI; telemetry (improvement stats) goes
separately to telemetry_log.py and is not duplicated on-screen.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

try:
    from .observer_protocol import build_event, parse_event_line
except ImportError:  # pragma: no cover - supports direct module execution
    from observer_protocol import build_event, parse_event_line

_GUI_ROOT = Path(".awos") / "gui"


class EventType:
    """String constants for all structured event types emitted by the orchestrator."""
    SESSION_START = "session_start"
    CODEBASE_INDEXED = "codebase_indexed"
    PLAN_GENERATED = "plan_generated"
    MODEL_ROUTED = "model_routed"
    TASK_START = "task_start"
    FILE_EDIT = "file_edit"
    TEST_RESULT = "test_result"
    TASK_COMPLETE = "task_complete"
    SESSION_DONE = "session_done"
    AGENT_THINKING = "agent_thinking"
    AGENT_TOOL_CALL = "agent_tool_call"
    IDLE = "idle"
    ERROR = "error"

    @classmethod
    def all(cls) -> set[str]:
        return {
            v for k, v in vars(cls).items()
            if not k.startswith("_") and isinstance(v, str)
        }


class UIEventType:
    """Semantic browser/UI actions; keystrokes are intentionally excluded."""

    APP_OPENED = "app_opened"
    VIEW_CHANGED = "view_changed"
    CHAT_SUBMITTED = "chat_submitted"
    CHAT_RESPONSE_STARTED = "chat_response_started"
    CHAT_RESPONSE_COMPLETED = "chat_response_completed"
    SESSION_SELECTED = "session_selected"
    APPROVAL = "approval"
    TASK_CONTROL = "task_control"
    CONTROL_REQUESTED = "control_requested"
    CONTROL_COMPLETED = "control_completed"
    CONTROL_REJECTED = "control_rejected"

    @classmethod
    def all(cls) -> set[str]:
        return {
            value for name, value in vars(cls).items()
            if not name.startswith("_") and isinstance(value, str)
        }


@dataclass
class GuiEvent:
    """Backward-compatible event object backed by the observer envelope."""

    type: str
    session_id: str
    timestamp: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex}")
    sequence: int = 0
    source: str = "agent"
    visibility: str = "assistant_safe"
    chat_id: str | None = None
    task_id: str | None = None
    trace_id: str | None = None
    span_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        event = build_event(
            event_type=self.type,
            session_id=self.session_id,
            payload=self.payload,
            sequence=self.sequence,
            source=self.source,
            visibility=self.visibility,
            event_id=self.event_id or None,
            emitted_at=self.timestamp,
            chat_id=self.chat_id,
            task_id=self.task_id,
            trace_id=self.trace_id,
            span_id=self.span_id,
        )
        # Keep the old field during migration. New observer consumers use
        # emitted_at; existing renderer code can continue reading timestamp.
        event["timestamp"] = event["emitted_at"]
        return event


class GuiEventBus:
    """Per-run event bus: persists events and notifies live subscribers (SSE/WebSocket)."""

    def __init__(
        self,
        session_id: str,
        goal: str = "",
        gui_root: Optional[Path] = None,
        *,
        chat_id: str | None = None,
        trace_id: str | None = None,
    ):
        self.session_id = session_id
        self.goal = goal
        self.chat_id = chat_id
        self.trace_id = trace_id or f"trace_{uuid.uuid4().hex}"
        self._root = (gui_root or _GUI_ROOT) / session_id
        self._root.mkdir(parents=True, exist_ok=True)
        self._events_path = self._root / "events.jsonl"
        self._manifest_path = self._root / "manifest.json"
        self._subscribers: list[Callable[[GuiEvent], None]] = []
        self._file_changes: list[dict[str, Any]] = []
        self._next_sequence = self._load_next_sequence()
        self._init_manifest()

    def _load_next_sequence(self) -> int:
        """Recover the next cursor from valid persisted observer envelopes."""
        if not self._events_path.exists():
            return 1
        highest = 0
        try:
            for line in self._events_path.read_text(encoding="utf-8").splitlines():
                event = parse_event_line(line)
                if event is not None:
                    highest = max(highest, event["sequence"])
        except OSError:
            return 1
        return highest + 1

    def _init_manifest(self) -> None:
        if self._manifest_path.exists():
            return
        self._write_manifest(
            {
                "session_id": self.session_id,
                "goal": self.goal,
                "status": "running",
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "files": [],
                "summary": {
                    "total_files": 0,
                    "created": 0,
                    "modified": 0,
                    "deleted": 0,
                    "lines_added": 0,
                    "lines_removed": 0,
                },
            }
        )

    def subscribe(self, callback: Callable[[GuiEvent], None]) -> None:
        self._subscribers.append(callback)

    def emit(
        self,
        event_type: str,
        payload: Optional[dict[str, Any]] = None,
        *,
        source: str = "agent",
        visibility: str = "assistant_safe",
        chat_id: str | None = None,
        task_id: str | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> GuiEvent:
        event = GuiEvent(
            type=event_type,
            session_id=self.session_id,
            payload=payload or {},
            sequence=self._next_sequence,
            source=source,
            visibility=visibility,
            chat_id=chat_id if chat_id is not None else self.chat_id,
            task_id=task_id,
            trace_id=trace_id if trace_id is not None else self.trace_id,
            span_id=span_id,
        )
        serialized = json.dumps(event.to_dict(), ensure_ascii=False)
        with open(self._events_path, "a", encoding="utf-8") as f:
            f.write(serialized + "\n")
        self._next_sequence += 1
        for cb in self._subscribers:
            try:
                cb(event)
            except Exception:
                pass
        return event

    # ── Structured emit helpers ──────────────────────────────────────────

    def emit_session_start(self, goal: str, codebase_root: str) -> GuiEvent:
        """Session started. Emitted once at the start of execute_feature()."""
        return self.emit(EventType.SESSION_START, {
            "goal": goal,
            "session_id": self.session_id,
            "codebase_root": codebase_root,
        })

    def emit_codebase_indexed(self, n_chunks: int, elapsed: float) -> GuiEvent:
        """Codebase vector index complete."""
        return self.emit(EventType.CODEBASE_INDEXED, {
            "n_chunks": n_chunks,
            "elapsed": elapsed,
        })

    def emit_plan_generated(self, n_tasks: int, tasks: list[dict]) -> GuiEvent:
        """Plan generated with N tasks."""
        return self.emit(EventType.PLAN_GENERATED, {
            "n_tasks": n_tasks,
            "tasks": tasks,
        })

    def emit_model_routed(self, model_name: str, tier: str, reason: str,
                          cost_estimate: float, provider: str) -> GuiEvent:
        """Model routing decision made by EscalationEngine."""
        return self.emit(EventType.MODEL_ROUTED, {
            "model_name": model_name,
            "tier": tier,
            "reason": reason,
            "cost_estimate": cost_estimate,
            "provider": provider,
        })

    def emit_task_start(self, task_id: int, action: str, file: str,
                        model_name: str, model_reason: str) -> GuiEvent:
        """Task execution started."""
        return self.emit(
            EventType.TASK_START,
            {
                "task_id": task_id,
                "action": action,
                "file": file,
                "model_name": model_name,
                "model_reason": model_reason,
            },
            task_id=str(task_id),
        )

    def emit_file_edit(self, path: str, status: str, lines_added: int,
                       lines_removed: int, diff: str = "") -> GuiEvent:
        """File modified by the agent."""
        return self.emit(EventType.FILE_EDIT, {
            "path": path,
            "status": status,
            "lines_added": lines_added,
            "lines_removed": lines_removed,
            "diff": diff,
        })

    def emit_test_result(self, test_name: str, passed: bool,
                         duration: float, output: str = "") -> GuiEvent:
        """Test completed (pass or fail)."""
        return self.emit(EventType.TEST_RESULT, {
            "test_name": test_name,
            "passed": passed,
            "duration": duration,
            "output": output,
        })

    def emit_task_complete(self, task_id: int, success: bool, cost_usd: float,
                           n_edits: int, error: str = "") -> GuiEvent:
        """Task execution finished."""
        return self.emit(
            EventType.TASK_COMPLETE,
            {
                "task_id": task_id,
                "success": success,
                "cost_usd": cost_usd,
                "n_edits": n_edits,
                "error": error,
            },
            task_id=str(task_id),
        )

    def emit_session_done(self, completed: int, failed: int,
                          total_cost: float, elapsed: float) -> GuiEvent:
        """Session completed. Emitted once at the end of execute_feature()."""
        return self.emit(EventType.SESSION_DONE, {
            "completed": completed,
            "failed": failed,
            "total_cost": total_cost,
            "elapsed": elapsed,
        })

    def emit_agent_thinking(self, thought: str, turn: int = 0) -> GuiEvent:
        """Record internal diagnostics as developer-only, never assistant-safe."""
        return self.emit(
            EventType.AGENT_THINKING,
            {
                "thought": thought,
                "turn": turn,
            },
            visibility="developer_only",
        )

    def emit_agent_tool_call(self, action: str, action_input: dict,
                             observation: str, success: bool,
                             latency_ms: float, turn: int = 0) -> GuiEvent:
        """Tool call made by the agent during a ReAct turn."""
        return self.emit(EventType.AGENT_TOOL_CALL, {
            "action": action,
            "action_input": action_input,
            "observation": observation[:500],
            "success": success,
            "latency_ms": latency_ms,
            "turn": turn,
        })

    def record_file_change(self, change: dict[str, Any]) -> None:
        self._file_changes.append(change)
        self.emit("file_change", change)
        self._refresh_manifest_files()

    def _refresh_manifest_files(self) -> None:
        try:
            from .diff_builder import FileChange, collect_session_files
        except ImportError:
            from diff_builder import FileChange, collect_session_files

        changes = [
            FileChange(
                path=c["path"],
                status=c.get("status", "modified"),
                lines_added=c.get("lines_added", 0),
                lines_removed=c.get("lines_removed", 0),
                hunks=[],
                unified_diff=c.get("unified_diff", ""),
            )
            for c in self._file_changes
        ]
        agg = collect_session_files(changes)
        manifest = self.get_manifest()
        # Keep full hunks/unified_diff for GUI — summary from lightweight copies
        manifest["files"] = self._file_changes
        manifest["summary"] = agg["summary"]
        self._write_manifest(manifest)

    def session_done(
        self,
        *,
        completed: int,
        failed: int,
        total_cost: float,
        elapsed: float,
    ) -> None:
        manifest = self.get_manifest()
        manifest["status"] = "completed" if failed == 0 else "partial"
        manifest["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        manifest["tasks_completed"] = completed
        manifest["tasks_failed"] = failed
        manifest["total_cost_usd"] = total_cost
        manifest["elapsed_sec"] = elapsed
        self._write_manifest(manifest)
        self.emit(
            "session_done",
            {
                "completed": completed,
                "failed": failed,
                "total_cost": total_cost,
                "elapsed": elapsed,
                "files_summary": manifest.get("summary", {}),
            },
        )

    def get_manifest(self) -> dict[str, Any]:
        if not self._manifest_path.exists():
            return {}
        return json.loads(self._manifest_path.read_text(encoding="utf-8"))

    def _write_manifest(self, data: dict[str, Any]) -> None:
        self._manifest_path.write_text(
            json.dumps(data, indent=2, default=str),
            encoding="utf-8",
        )

    def list_events(self) -> list[dict[str, Any]]:
        if not self._events_path.exists():
            return []
        out = []
        for line in self._events_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    @staticmethod
    def list_sessions(gui_root: Optional[Path] = None) -> list[dict[str, Any]]:
        root = gui_root or _GUI_ROOT
        if not root.exists():
            return []
        sessions = []
        for d in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not d.is_dir():
                continue
            manifest_path = d / "manifest.json"
            if manifest_path.exists():
                try:
                    sessions.append(json.loads(manifest_path.read_text(encoding="utf-8")))
                except json.JSONDecodeError:
                    continue
        return sessions
