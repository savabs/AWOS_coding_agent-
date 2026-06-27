"""
gui_events.py — Structured events for the AWOS GUI.

LiveRenderer and the orchestrator emit events here. Events are persisted to
.awos/gui/<session_id>/ for the web UI; telemetry (improvement stats) goes
separately to telemetry_log.py and is not duplicated on-screen.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


_GUI_ROOT = Path(".awos") / "gui"


@dataclass
class GuiEvent:
    type: str
    session_id: str
    timestamp: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }


class GuiEventBus:
    """Per-run event bus: persists events and notifies live subscribers (SSE/WebSocket)."""

    def __init__(self, session_id: str, goal: str = "", gui_root: Optional[Path] = None):
        self.session_id = session_id
        self.goal = goal
        self._root = (gui_root or _GUI_ROOT) / session_id
        self._root.mkdir(parents=True, exist_ok=True)
        self._events_path = self._root / "events.jsonl"
        self._manifest_path = self._root / "manifest.json"
        self._subscribers: list[Callable[[GuiEvent], None]] = []
        self._file_changes: list[dict[str, Any]] = []
        self._init_manifest()

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

    def emit(self, event_type: str, payload: Optional[dict[str, Any]] = None) -> GuiEvent:
        event = GuiEvent(
            type=event_type,
            session_id=self.session_id,
            payload=payload or {},
        )
        with open(self._events_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event.to_dict(), default=str) + "\n")
        for cb in self._subscribers:
            try:
                cb(event)
            except Exception:
                pass
        return event

    def record_file_change(self, change: dict[str, Any]) -> None:
        self._file_changes.append(change)
        self.emit("file_change", change)
        self._refresh_manifest_files()

    def _refresh_manifest_files(self) -> None:
        try:
            from .diff_builder import collect_session_files, FileChange
        except ImportError:
            from diff_builder import collect_session_files, FileChange

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
