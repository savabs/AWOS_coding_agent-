#!/usr/bin/env python3
"""
AWOS GUI server — chat + run viewer.

Usage:
    python3 gui/server.py [--port 8765]

API:
    GET  /api/sessions              — list orchestrator runs
    GET  /api/sessions/<id>/files   — file diffs for a run
    GET    /api/chat/sessions         — list chat sessions
    GET    /api/chat/sessions/<id>    — single chat session
    DELETE /api/chat/sessions         — delete all chat sessions
    DELETE /api/chat/sessions/<id>    — delete one chat session
    POST   /api/chat/sessions/clear   — delete all (preferred; always supported)
    POST   /api/chat/sessions/delete  — delete one {chat_id}
    POST /api/chat                  — send message {message, mode, chat_id?}
    POST /api/chat/stream           — SSE stream {message, mode, chat_id?}
    POST /api/chat/stop             — stop active stream {chat_id}
    GET  /api/cache/stats           — response cache stats
"""

from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote
from typing import Callable, Iterator

ROOT = Path(__file__).resolve().parent.parent
GUI_ROOT = ROOT / ".awos" / "gui"
STATIC = Path(__file__).resolve().parent / "static"

# Load .env for API keys
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

# Repo root on path so `scaffold.agent.*` imports work (same as awos.py)
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

# Initialize logger
logger = logging.getLogger(__name__)

from scaffold.agent.gui_events import GuiEventBus  # noqa: E402
from scaffold.agent.gui_chat import GuiChatService, MODE_LABELS, VALID_MODES  # noqa: E402

_chat_service: GuiChatService | None = None


def get_chat_service() -> GuiChatService:
    global _chat_service
    if _chat_service is None:
        _chat_service = GuiChatService(codebase_root=str(ROOT))
    return _chat_service


class GuiHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        pass

    def _send_json(self, data: object, status: int = 200) -> None:
        body = json.dumps(data, indent=2, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_sse(
        self,
        events: Iterator[dict],
        on_disconnect: Callable[[], None] | None = None,
    ) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        gen = iter(events)
        try:
            for item in gen:
                event_type = item.get("event", "message")
                data = json.dumps(item.get("data", {}), default=str)
                chunk = f"event: {event_type}\ndata: {data}\n\n".encode("utf-8")
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            if on_disconnect:
                on_disconnect()
            for _ in gen:
                pass

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _send_file(self, path: Path) -> None:
        if not path.is_file():
            self._send_json({"error": "not found"}, 404)
            return
        mime, _ = mimetypes.guess_type(str(path))
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_sse_events(self) -> None:
        """SSE endpoint that tails .awos/gui/<session>/events.jsonl.

        Tracks byte offset per session so we only send new events.
        Sends structured events that agent.html renders by type.
        Falls back to idle keepalive when no active session.
        """
        import time
        from pathlib import Path

        _GUI_ROOT = Path(".awos") / "gui"

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        # Track per-session byte offsets
        _offsets: dict[str, int] = {}
        _last_session_id: str | None = None

        def _find_latest_session() -> str | None:
            """Return the most recent session directory name (by mtime)."""
            if not _GUI_ROOT.is_dir():
                return None
            dirs = sorted(
                [d for d in _GUI_ROOT.iterdir() if d.is_dir()],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            return dirs[0].name if dirs else None

        def _read_new_events(session_id: str) -> list[dict]:
            """Read new events from events.jsonl since last offset."""
            events_path = _GUI_ROOT / session_id / "events.jsonl"
            if not events_path.is_file():
                return []
            current_size = events_path.stat().st_size
            last_offset = _offsets.get(session_id, 0)
            if current_size <= last_offset:
                return []
            with open(events_path, "r", encoding="utf-8") as f:
                f.seek(last_offset)
                new_lines = f.read()
                _offsets[session_id] = f.tell()
            events = []
            for line in new_lines.splitlines():
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            return events

        try:
            while True:
                session_id = _find_latest_session()

                if session_id is None:
                    # No sessions at all — send idle keepalive
                    self._sse_send({"type": "idle", "payload": {"message": "No active sessions"}})
                    time.sleep(2.0)
                    continue

                # Reset offset if session changed (new run)
                if session_id != _last_session_id:
                    _last_session_id = session_id
                    _offsets[session_id] = 0

                # Read new events
                events = _read_new_events(session_id)

                if events:
                    for event in events:
                        self._sse_send(event)
                else:
                    self._sse_send({"type": "idle", "payload": {"message": "Waiting for events..."}})

                time.sleep(0.5)

        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            logger.error(f"SSE error: {e}")

    def _sse_send(self, data: dict) -> None:
        """Send one SSE data frame."""
        message = f"data: {json.dumps(data, default=str)}\n\n"
        try:
            self.wfile.write(message.encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            raise

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self) -> None:
        path = unquote(self.path.split("?", 1)[0])

        if path == "/api/chat/stream":
            try:
                body = self._read_json_body()
                svc = get_chat_service()
                chat_id_holder: dict[str, str | None] = {"id": body.get("chat_id")}

                def events() -> Iterator[dict]:
                    for evt in svc.handle_message_stream(
                        message=body.get("message", ""),
                        mode=body.get("mode", "qa"),
                        chat_id=body.get("chat_id"),
                    ):
                        if evt.get("event") == "start":
                            chat_id_holder["id"] = evt.get("data", {}).get("chat_id")
                        yield evt

                def on_disconnect() -> None:
                    cid = chat_id_holder.get("id")
                    if cid:
                        svc.stop_run(cid)

                self._send_sse(events(), on_disconnect=on_disconnect)
            except json.JSONDecodeError:
                self._send_json({"error": "invalid JSON body"}, 400)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return

        if path == "/api/chat/stop":
            try:
                body = self._read_json_body()
                chat_id = body.get("chat_id")
                if not chat_id:
                    self._send_json({"error": "chat_id required"}, 400)
                    return
                stopped = get_chat_service().stop_run(chat_id)
                self._send_json({"stopped": stopped, "chat_id": chat_id})
            except json.JSONDecodeError:
                self._send_json({"error": "invalid JSON body"}, 400)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return

        if path == "/api/chat/sessions/clear":
            deleted = get_chat_service().store.delete_all()
            self._send_json({"deleted": deleted})
            return

        if path == "/api/chat/sessions/delete":
            try:
                body = self._read_json_body()
                chat_id = body.get("chat_id")
                if not chat_id:
                    self._send_json({"error": "chat_id required"}, 400)
                    return
                if get_chat_service().store.delete(chat_id):
                    self._send_json({"deleted": chat_id})
                else:
                    self._send_json({"error": "chat not found"}, 404)
            except json.JSONDecodeError:
                self._send_json({"error": "invalid JSON body"}, 400)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return

        if path == "/api/chat":
            try:
                body = self._read_json_body()
                if body.get("stream"):
                    svc = get_chat_service()
                    chat_id_holder: dict[str, str | None] = {"id": body.get("chat_id")}

                    def events() -> Iterator[dict]:
                        for evt in svc.handle_message_stream(
                            message=body.get("message", ""),
                            mode=body.get("mode", "qa"),
                            chat_id=body.get("chat_id"),
                        ):
                            if evt.get("event") == "start":
                                chat_id_holder["id"] = evt.get("data", {}).get("chat_id")
                            yield evt

                    def on_disconnect() -> None:
                        cid = chat_id_holder.get("id")
                        if cid:
                            svc.stop_run(cid)

                    self._send_sse(events(), on_disconnect=on_disconnect)
                    return
                result = get_chat_service().handle_message(
                    message=body.get("message", ""),
                    mode=body.get("mode", "qa"),
                    chat_id=body.get("chat_id"),
                )
                status = int(result.pop("status", 200))
                if "error" in result and status >= 400:
                    self._send_json(result, status)
                else:
                    self._send_json(result, status)
            except json.JSONDecodeError:
                self._send_json({"error": "invalid JSON body"}, 400)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return

        self._send_json({"error": "not found"}, 404)

    def do_DELETE(self) -> None:
        path = unquote(self.path.split("?", 1)[0])

        if path == "/api/chat/sessions":
            deleted = get_chat_service().store.delete_all()
            self._send_json({"deleted": deleted})
            return

        if path.startswith("/api/chat/sessions/"):
            parts = path.strip("/").split("/")
            if len(parts) == 4:
                chat_id = parts[3]
                if get_chat_service().store.delete(chat_id):
                    self._send_json({"deleted": chat_id})
                else:
                    self._send_json({"error": "chat not found"}, 404)
                return

        self._send_json({"error": "not found"}, 404)

    def _get_ci_rescue_stats(self) -> dict:
        """Get CI rescue statistics from runtime sessions."""
        from scaffold.agent.runtime_session import RuntimeSessionStore
        
        store = RuntimeSessionStore()
        sessions = store.list_sessions()
        
        # Filter for CI rescue sessions (goal contains "CI Rescue")
        ci_sessions = [s for s in sessions if "CI Rescue" in (s.goal or "")]
        
        total_rescues = len(ci_sessions)
        total_tests_fixed = 0
        total_cost = 0.0
        
        for session in ci_sessions:
            # Count completed tasks as tests fixed
            total_tests_fixed += len(session.progress.completed_task_ids)
            # Sum up costs
            total_cost += session.budget.get("spent_usd", 0.0)
        
        # Calculate trends (compare last 5 vs previous 5)
        trend_rescues = ""
        trend_tests = ""
        trend_cost = ""
        trend_savings = ""
        
        if total_rescues >= 10:
            recent = ci_sessions[:5]
            previous = ci_sessions[5:10]
            
            recent_tests = sum(len(s.progress.completed_task_ids) for s in recent)
            previous_tests = sum(len(s.progress.completed_task_ids) for s in previous)
            
            if recent_tests > previous_tests:
                trend_tests = f"+{recent_tests - previous_tests} vs previous"
            elif recent_tests < previous_tests:
                trend_tests = f"{recent_tests - previous_tests} vs previous"
        
        return {
            "total_rescues": total_rescues,
            "total_tests_fixed": total_tests_fixed,
            "total_cost_usd": total_cost,
            "trend_rescues": trend_rescues,
            "trend_tests": trend_tests,
            "trend_cost": trend_cost,
            "trend_savings": trend_savings,
        }

    def _get_ci_rescue_history(self) -> list:
        """Get CI rescue session history."""
        from scaffold.agent.runtime_session import RuntimeSessionStore
        
        store = RuntimeSessionStore()
        sessions = store.list_sessions()
        
        # Filter for CI rescue sessions
        ci_sessions = [s for s in sessions if "CI Rescue" in (s.goal or "")]
        
        # Sort by timestamp (most recent first)
        ci_sessions.sort(key=lambda s: s.updated_at or "", reverse=True)
        
        history = []
        for session in ci_sessions[:20]:  # Last 20 rescues
            history.append({
                "session_id": session.session_id,
                "goal": session.goal,
                "timestamp": session.updated_at or session.created_at,
                "status": session.status.value,
                "success": session.status.value == "completed",
                "tasks_completed": len(session.progress.completed_task_ids),
                "tasks_failed": len(session.progress.failed_task_ids),
                "total_cost_usd": session.budget.get("spent_usd", 0.0),
                "tests_fixed": len(session.progress.completed_task_ids),
            })
        
        return history

    def do_GET(self) -> None:
        path = unquote(self.path.split("?", 1)[0])

        # SSE endpoint for real-time agent events
        if path == "/api/agent/stream":
            self._handle_sse_events()
            return

        if path == "/api/agent/events":
            self._handle_sse_events()
            return

        if path == "/api/cache/stats":
            self._send_json(get_chat_service().response_cache.stats())
            return

        if path == "/api/ci-rescue/stats":
            try:
                stats = self._get_ci_rescue_stats()
                self._send_json(stats)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return

        if path == "/api/ci-rescue/history":
            try:
                history = self._get_ci_rescue_history()
                self._send_json(history)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return

        if path == "/api/metrics/summary":
            try:
                from scaffold.agent.pei_report import PEIReport
                snap = PEIReport(store_path=str(ROOT / ".awos"), project_name="AWOS").snapshot()
                self._send_json(snap.to_dict())
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return

        if path == "/api/modes":
            self._send_json({
                "modes": [
                    {"id": m, "label": MODE_LABELS[m], "description": _mode_description(m)}
                    for m in VALID_MODES
                ]
            })
            return

        if path == "/api/chat/sessions":
            sessions = get_chat_service().store.list_sessions()
            self._send_json(sessions)
            return

        if path.startswith("/api/chat/sessions/"):
            parts = path.strip("/").split("/")
            if len(parts) == 4:
                chat_id = parts[3]
                session = get_chat_service().store.load(chat_id)
                if session is None:
                    self._send_json({"error": "chat not found"}, 404)
                    return
                self._send_json(session.to_dict())
                return

        if path == "/api/sessions":
            self._send_json(GuiEventBus.list_sessions(GUI_ROOT))
            return

        if path.startswith("/api/sessions/"):
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "api" and parts[1] == "sessions":
                sid = parts[2]
                manifest_path = GUI_ROOT / sid / "manifest.json"
                if not manifest_path.exists():
                    self._send_json({"error": "session not found"}, 404)
                    return
                self._send_json(json.loads(manifest_path.read_text(encoding="utf-8")))
                return

            if len(parts) == 4 and parts[3] == "events":
                sid = parts[2]
                bus = GuiEventBus(sid)
                self._send_json(bus.list_events())
                return

            if len(parts) == 4 and parts[3] == "files":
                sid = parts[2]
                manifest_path = GUI_ROOT / sid / "manifest.json"
                if not manifest_path.exists():
                    self._send_json({"error": "session not found"}, 404)
                    return
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                self._send_json({
                    "files": manifest.get("files", []),
                    "summary": manifest.get("summary", {}),
                })
                return

        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        if path in ("/", "/index.html"):
            self._send_file(STATIC / "index.html")
            return

        if path in ("/agent", "/agent.html"):
            self._send_file(STATIC / "agent.html")
            return

        static_path = STATIC / path.lstrip("/")
        if static_path.is_file() and STATIC in static_path.resolve().parents:
            self._send_file(static_path)
            return

        self._send_json({"error": "not found"}, 404)


def _mode_description(mode: str) -> str:
    return {
        "qa": "Ask questions — explains the repo, no code changes",
        "plan": "Break a goal into tasks — preview only, no execution",
        "agent": "Run the full agent — plans, edits files, shows diffs",
    }.get(mode, "")


def main() -> None:
    parser = argparse.ArgumentParser(description="AWOS GUI server")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), GuiHandler)
    print(f"AWOS GUI → http://{args.host}:{args.port}")
    print(f"  Agent: http://{args.host}:{args.port}/agent")
    print(f"  Chat:  http://{args.host}:{args.port}/?view=chat")
    print(f"  Runs:  http://{args.host}:{args.port}/?view=runs")
    print(f"Sessions: {GUI_ROOT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
