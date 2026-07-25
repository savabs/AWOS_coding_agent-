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
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote
from typing import Callable, Iterator

ROOT = Path(__file__).resolve().parent.parent
GUI_ROOT = ROOT / ".awos" / "gui"
STATIC = Path(__file__).resolve().parent / "static"

# Load .env for API keys
print(f"[server] Starting with cwd={os.getcwd()}", file=sys.stderr, flush=True)
print(f"[server] ROOT={ROOT}", file=sys.stderr, flush=True)
print(f"[server] .env exists={os.path.exists(ROOT / '.env')}", file=sys.stderr, flush=True)
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
    print(f"[server] After load_dotenv: OPENCODE_GO_API_KEY={os.environ.get('OPENCODE_GO_API_KEY', 'NOT SET')[:20]}...", file=sys.stderr, flush=True)
    # Explicitly parse and set env vars to ensure they're available for subprocesses
    with open(ROOT / ".env") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ[_k.strip()] = _v.strip()
    print(f"[server] After manual parse: OPENCODE_GO_API_KEY={os.environ.get('OPENCODE_GO_API_KEY', 'NOT SET')[:20]}...", file=sys.stderr, flush=True)
except ImportError as e:
    print(f"[server] dotenv not installed: {e}", file=sys.stderr, flush=True)
except Exception as _e:
    print(f"[server] .env load error: {_e}", file=sys.stderr, flush=True)

# Repo root on path so `scaffold.agent.*` imports work (same as awos.py)
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

# Initialize logger
logger = logging.getLogger(__name__)

from scaffold.agent.gui_events import GuiEventBus  # noqa: E402
from scaffold.agent.gui_chat import GuiChatService, MODE_LABELS, VALID_MODES  # noqa: E402
from scaffold.agent.diagnostics import TraceSpan, TimingReport  # noqa: E402

# ── Subprocess tracking (for crash detection) ─────────────────────────────────
# Each /api/agent/run spawns an orchestrator subprocess. We track the Popen
# handle so the SSE loop can detect when a subprocess dies without writing a
# session_done event (orchestrator crash, OOM, etc.) and surface a visible
# error in the UI instead of leaving the client hanging. Class-level so the
# dict is shared across request handler instances.
_running_orchestrators: dict[str, subprocess.Popen] = {}

# Lazy-loaded: EscalationEngine + routing deps (heavy imports, only for /api/routing/*)
_routing_engine: object | None = None
_routing_store: object | None = None


def _get_routing_deps() -> tuple[object, object]:
    """Lazy-load EscalationEngine + RewardStore for routing API."""
    global _routing_engine, _routing_store
    if _routing_engine is None:
        from scaffold.agent.escalation_engine import EscalationEngine
        from scaffold.agent.reward_store import RewardStore
        from scaffold.agent.ml_router import build_ml_router
        from pathlib import Path as _Path

        store = RewardStore()
        router = build_ml_router(
            min_samples=20,
            weights_path=_Path(".awos") / "linucb_weights.pkl",
            gp_path=_Path(".awos") / "gp_model.pkl",
        )
        router.warm_start(store, max_episodes=50)
        engine = EscalationEngine(ml_router=router)

        _routing_store = store
        _routing_engine = engine

    return _routing_engine, _routing_store

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
            try:
                for _ in gen:
                    pass
            except Exception:
                pass  # Suppress downstream errors during client disconnect

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
                        # If the orchestrator finishes cleanly, drop the
                        # tracked process so we don't try to report it as
                        # crashed later.
                        if event.get("type") == "session_done":
                            for rid in list(_running_orchestrators.keys()):
                                proc = _running_orchestrators[rid]
                                if proc.poll() is not None:
                                    del _running_orchestrators[rid]
                else:
                    self._sse_send({"type": "idle", "payload": {"message": "Waiting for events..."}})

                # ── Crash detection ─────────────────────────────────────────
                # On each iteration, check tracked Popen handles. If one
                # has died without a session_done event, emit a synthetic
                # error so the UI can show a toast instead of stalling.
                for rid in list(_running_orchestrators.keys()):
                    proc = _running_orchestrators[rid]
                    rc = proc.poll()
                    if rc is not None:
                        # Process has exited. If exit code != 0 and we
                        # haven't seen a session_done, surface as crash.
                        # Skip the latest session if it just completed
                        # normally (race condition).
                        if rc != 0:
                            self._sse_send({
                                "type": "error",
                                "payload": {
                                    "message": f"Orchestrator exited unexpectedly with code {rc}",
                                    "run_id": rid,
                                },
                            })
                        del _running_orchestrators[rid]

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

        if path == "/api/agent/run":
            try:
                body = self._read_json_body()
                goal = body.get("goal", "").strip()
                codebase_root = body.get("root", str(ROOT))
                if not goal:
                    self._send_json({"error": "goal is required"}, 400)
                    return
                # Spawn awos run as background subprocess with logging
                import uuid
                run_id = str(uuid.uuid4())[:8]
                log_file = ROOT / ".awos" / "gui" / f"orch_{run_id}.log"
                log_file.parent.mkdir(parents=True, exist_ok=True)
                with open(log_file, "w") as lf:
                    lf.write(f"Started: {goal}\n")
                proc = subprocess.Popen(
                    [sys.executable, "-m", "awos", "run", goal, "--root", codebase_root],
                    cwd=ROOT,
                    env={
                        **os.environ,
                        # Disable the worktree sandbox for web UI tasks.
                        # The orchestrator creates a worktree at
                        # .awos/worktrees/<id>/ that isolates file edits.
                        # For interactive web UI use (file paths relative
                        # to the project root), this isolation causes
                        # "File not found" errors because dotfiles and
                        # non-codebase files aren't copied to the sandbox.
                        "AWOS_USE_WORKTREE": "false",
                    },
                    stdout=open(log_file, "a"),
                    stderr=open(log_file, "a"),
                )
                # Track for crash detection (SSE loop checks this on each
                # iteration). Removed when a session_done event is observed.
                _running_orchestrators[run_id] = proc
                self._send_json({"status": "started", "goal": goal, "root": codebase_root, "run_id": run_id})
            except json.JSONDecodeError:
                self._send_json({"error": "invalid JSON body"}, 400)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return

        if path == "/api/chat/stream":
            span = TraceSpan("chat_stream", "http")
            try:
                body = self._read_json_body()
                svc = get_chat_service()
                chat_id_holder: dict[str, str | None] = {"id": body.get("chat_id")}

                def events() -> Iterator[dict]:
                    t0 = time.perf_counter()
                    first = True
                    for evt in svc.handle_message_stream(
                        message=body.get("message", ""),
                        mode=body.get("mode", "qa"),
                        chat_id=body.get("chat_id"),
                        model=body.get("model") or None,
                    ):
                        if evt.get("event") == "start":
                            chat_id_holder["id"] = evt.get("data", {}).get("chat_id")
                        if first and evt.get("event") == "token":
                            ttfb = (time.perf_counter() - t0) * 1000
                            span.metadata["ttfb_ms"] = round(ttfb, 1)
                            first = False
                        if evt.get("event") == "done" or evt.get("event") == "error":
                            span.done(
                                success=evt.get("event") != "error",
                                error=str(evt.get("data", {}).get("error", ""))[:100] if evt.get("event") == "error" else "",
                                mode=body.get("mode", "qa"),
                                total_ms=round((time.perf_counter() - t0) * 1000, 1)
                            )
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

    def _get_routing_status(self) -> dict:
        """Return transparent routing status for the dashboard.

        Combines EscalationEngine.routing_status() (live LinUCB state) with
        RewardStore summary (per-model historical performance).
        """
        engine, store = _get_routing_deps()

        # Live routing state
        status = engine.routing_status()

        # Historical performance from RewardStore
        from scaffold.agent.reward_store import RewardStore as _RS
        perf: dict = {"by_model": {}, "total_episodes": 0}
        try:
            rstore: _RS = store  # type: ignore[assignment]
            total = rstore.total_episodes()
            perf["total_episodes"] = total
            if total > 0:
                episodes = rstore.get_recent(min(total, 500))
                by_model: dict[int, dict] = {}
                for ep in episodes:
                    aid = ep.action_id
                    if aid not in by_model:
                        by_model[aid] = {"attempts": 0, "successes": 0, "total_cost": 0.0}
                    by_model[aid]["attempts"] += 1
                    by_model[aid]["successes"] += int(ep.success)
                    by_model[aid]["total_cost"] += ep.cost_usd

                from scaffold.agent.ml_router import ACTION_NAMES
                for aid, data in sorted(by_model.items()):
                    name = ACTION_NAMES[aid] if aid < len(ACTION_NAMES) else str(aid)
                    perf["by_model"][name] = {
                        "attempts": data["attempts"],
                        "success_rate": round(data["successes"] / data["attempts"], 3),
                        "avg_cost_usd": round(data["total_cost"] / data["attempts"], 5),
                        "total_cost_usd": round(data["total_cost"], 4),
                    }
        except Exception:
            pass

        return {
            "routing": status,
            "performance": perf,
            "timestamp": __import__("datetime").datetime.now().isoformat(),
        }

    def _get_routing_history(self, limit: int = 20) -> list[dict]:
        """Return recent routing decisions with outcomes for the dashboard."""
        _, store = _get_routing_deps()
        from scaffold.agent.reward_store import RewardStore as _RS
        from scaffold.agent.ml_router import ACTION_NAMES

        history: list[dict] = []
        try:
            rstore: _RS = store  # type: ignore[assignment]
            episodes = rstore.get_recent(limit)
            for ep in episodes:
                name = ACTION_NAMES[ep.action_id] if ep.action_id < len(ACTION_NAMES) else str(ep.action_id)
                history.append({
                    "episode_id": ep.episode_id[:8],
                    "task": ep.task_action_text[:80],
                    "model": name,
                    "success": ep.success,
                    "reward": ep.reward,
                    "cost_usd": ep.cost_usd,
                    "timestamp": ep.timestamp,
                })
        except Exception:
            pass

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

        if path == "/api/diagnostics":
            from scaffold.agent import diagnostics
            timing = TimingReport.by_phase()
            detail = TimingReport.by_name()
            self._send_json({"phases": timing, "details": detail, "trace_count": len(diagnostics.TRACES)})
            return

        if path == "/api/metrics/summary":
            try:
                from scaffold.agent.pei_report import PEIReport
                snap = PEIReport(store_path=str(ROOT / ".awos"), project_name="AWOS").snapshot()
                self._send_json(snap.to_dict())
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return

        if path == "/api/budget":
            try:
                from scaffold.agent.budget_ledger import get_ledger
                status = get_ledger().get_status()
                self._send_json(status)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return

        if path == "/api/diagnostics":
            from scaffold.agent import diagnostics
            timing = TimingReport.by_phase()
            detail = TimingReport.by_name()
            self._send_json({"phases": timing, "details": detail, "trace_count": len(diagnostics.TRACES)})
            return

        if path == "/api/health":
            from scaffold.agent.provider_health import check_all as _check_all
            results = _check_all()
            self._send_json({name: {"available": h.available, "balance_ok": h.balance_ok, "latency_ms": h.latency_ms, "error": h.error} for name, h in results.items()})
            return

        if path == "/api/models/status":
            """Return current model tier list with cached status."""
            from scaffold.agent.gui_chat import GuiChatService
            tiers = GuiChatService._OC_MODEL_TIERS
            self._send_json([{"model": m[0], "name": m[1], "protocol": m[4]} for m in tiers])
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

        if path == "/api/routing/status":
            try:
                self._send_json(self._get_routing_status())
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return

        if path == "/api/routing/history":
            try:
                self._send_json(self._get_routing_history(limit=30))
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
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
    # ── Startup health check (async, non-blocking) ────────────────────
    import threading
    def _startup_health():
        from scaffold.agent.provider_health import check_opencode_go, check_deepseek, check_anthropic
        import traceback
        while True:
            try:
                results = {}
                for name, fn in [("opencode", check_opencode_go), ("deepseek", check_deepseek), ("anthropic", check_anthropic)]:
                    try:
                        results[name] = fn()
                    except Exception as e:
                        results[name] = type('H',(),{'can_use':False,'latency_ms':0,'error':str(e)[:60]})()
                statuses = []
                for name, h in results.items():
                    icon = "✅" if getattr(h, 'can_use', False) else "❌"
                    statuses.append(f"  {icon} {name}: {getattr(h, 'latency_ms', 0):.0f}ms" + (f" — {getattr(h, 'error', '')[:60]}" if getattr(h, 'error', '') else ""))
                print("Provider health:\n" + "\n".join(statuses))
            except Exception as e:
                print(f"Health check error: {e}\n{traceback.format_exc()}")
            time.sleep(300)
    threading.Thread(target=_startup_health, daemon=True).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
