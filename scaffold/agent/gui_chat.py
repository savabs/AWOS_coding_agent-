"""
gui_chat.py — HTTP-facing chat bridge for the AWOS GUI.

Modes:
  qa     — Questions & explanations (no file edits)
  plan   — Task breakdown only (CheapPlanner, no execution)
  agent  — Full orchestrator run (plan → worker → verify → diffs)

Features:
  - SSE token streaming (word/chunk delivery, Hermes-style)
  - Session memory (multi-turn context from chat history)
  - ResponseCache (15 min TTL for repeated Q/A)
  - SOUL.xml rules/patterns injected into context
  - Agent mode streams live progress from events.jsonl
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Iterator, Literal, Optional

from scaffold.agent.response_cache import ResponseCache
from scaffold.agent.run_metrics import build_run_metrics

ChatMode = Literal["qa", "plan", "agent"]

CHAT_DIR = Path(".awos/gui_chat")
VALID_MODES = ("qa", "plan", "agent")

MODE_LABELS = {
    "qa": "Q/A",
    "plan": "Plan",
    "agent": "Agent",
}


def _chunk_text(text: str, size: int = 12) -> list[str]:
    """Split text into word-ish chunks for typing effect."""
    if not text:
        return []
    chunks: list[str] = []
    i = 0
    while i < len(text):
        end = min(i + size, len(text))
        if end < len(text):
            sp = text.rfind(" ", i, end)
            if sp > i:
                end = sp + 1
        chunks.append(text[i:end])
        i = end
    return chunks


def _estimate_tokens(text: str) -> int:
    """Rough token estimate when provider omits usage (≈4 chars/token)."""
    return max(1, len(text) // 4) if text else 0


def _session_token_stats(messages: list["ChatMessage"]) -> dict[str, int]:
    prompt = completion = 0
    for m in messages:
        if m.role != "assistant":
            continue
        meta = m.meta or {}
        prompt += int(meta.get("prompt_tokens", 0) or 0)
        completion += int(meta.get("completion_tokens", 0) or 0)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


def _format_agent_event(event_type: str, payload: dict[str, Any]) -> str:
    et = event_type or payload.get("type", "")
    if et == "planning_done":
        n = payload.get("task_count", payload.get("tasks", "?"))
        return f"Planning done — {n} task(s)"
    if et == "task_start":
        return f"Task {payload.get('task_id', '?')}: {payload.get('action', '')[:80]}"
    if et == "worker_start":
        return f"Worker running on `{payload.get('file', '?')}`"
    if et == "worker_done":
        return f"Worker finished `{payload.get('file', '?')}`"
    if et == "file_change":
        return f"Changed `{payload.get('path', '?')}` (+{payload.get('lines_added', 0)})"
    if et == "verify_result":
        ok = payload.get("passed", payload.get("success"))
        return "Verification passed" if ok else "Verification failed"
    if et == "task_done":
        return f"Task {payload.get('task_id', '?')} done"
    if et == "session_done":
        return "Session complete"
    return ""


def _agent_partial_reply(
    progress_lines: list[str],
    session_id: Optional[str] = None,
    partial_text: str = "",
) -> str:
    """Build a natural assistant message from work done before stop."""
    text = (partial_text or "").strip()
    if text:
        return text
    if not progress_lines:
        return ""
    body = "\n".join(f"- {line}" for line in progress_lines)
    if session_id:
        body += f"\n\n[View run →](?view=runs&session={session_id})"
    return body


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class ChatMessage:
    role: str
    content: str
    mode: str = "qa"
    timestamp: str = field(default_factory=_now)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChatSession:
    chat_id: str
    title: str = "New chat"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    messages: list[ChatMessage] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chat_id": self.chat_id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "messages": [m.to_dict() for m in self.messages],
            "token_stats": _session_token_stats(self.messages),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChatSession:
        msgs = [
            ChatMessage(**{k: v for k, v in m.items() if k in ChatMessage.__dataclass_fields__})
            for m in data.get("messages", [])
        ]
        return cls(
            chat_id=data["chat_id"],
            title=data.get("title", "New chat"),
            created_at=data.get("created_at", _now()),
            updated_at=data.get("updated_at", _now()),
            messages=msgs,
        )


class GuiChatStore:
    """Persist chat sessions to .awos/gui_chat/."""

    def __init__(self, root: Path | str = CHAT_DIR) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, chat_id: str) -> Path:
        return self.root / f"{chat_id}.json"

    def create(self, title: str = "New chat") -> ChatSession:
        chat_id = f"chat_{uuid.uuid4().hex[:8]}"
        session = ChatSession(chat_id=chat_id, title=title)
        self.save(session)
        return session

    def save(self, session: ChatSession) -> None:
        session.updated_at = _now()
        self._path(session.chat_id).write_text(
            json.dumps(session.to_dict(), indent=2) + "\n",
            encoding="utf-8",
        )

    def load(self, chat_id: str) -> Optional[ChatSession]:
        path = self._path(chat_id)
        if not path.exists():
            return None
        return ChatSession.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def delete(self, chat_id: str) -> bool:
        """Remove a chat session file. Returns True if it existed."""
        path = self._path(chat_id)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def delete_all(self) -> int:
        """Remove all chat session files. Returns count deleted."""
        deleted = 0
        for path in self.root.glob("chat_*.json"):
            try:
                path.unlink()
                deleted += 1
            except OSError:
                continue
        return deleted

    def list_sessions(self) -> list[dict[str, Any]]:
        sessions: list[dict[str, Any]] = []
        for path in self.root.glob("chat_*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                msgs = data.get("messages", [])
                token_stats = _session_token_stats([
                    ChatMessage(**{k: v for k, v in m.items() if k in ChatMessage.__dataclass_fields__})
                    for m in msgs
                ]) if msgs else {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0}
                sessions.append({
                    "chat_id": data.get("chat_id", path.stem),
                    "title": data.get("title", path.stem),
                    "updated_at": data.get("updated_at", ""),
                    "message_count": len(msgs),
                    "total_tokens": token_stats.get("total_tokens", 0),
                })
            except (json.JSONDecodeError, OSError):
                continue
        sessions.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
        return sessions


class GuiChatService:
    """Process chat messages for the GUI."""

    def __init__(self, codebase_root: str = ".", store: Optional[GuiChatStore] = None) -> None:
        self.codebase_root = codebase_root
        self.store = store or GuiChatStore(Path(codebase_root) / CHAT_DIR)
        self.response_cache = ResponseCache()
        self._last_cost = 0.0
        self._last_model = ""
        self._last_prompt_tokens = 0
        self._last_completion_tokens = 0
        self._active_runs: dict[str, threading.Event] = {}
        self._runtime_session_ids: dict[str, str] = {}
        self._runs_lock = threading.Lock()

    def start_run(self, chat_id: str) -> threading.Event:
        """Register an in-flight stream; returns its cancel event."""
        cancel = threading.Event()
        with self._runs_lock:
            prior = self._active_runs.get(chat_id)
            if prior is not None:
                prior.set()
            self._active_runs[chat_id] = cancel
        return cancel

    def stop_run(self, chat_id: str) -> bool:
        """Signal an active stream to stop. Returns True if a run was active."""
        with self._runs_lock:
            cancel = self._active_runs.get(chat_id)
            runtime_sid = self._runtime_session_ids.get(chat_id)
        if cancel is None:
            return False
        cancel.set()
        if runtime_sid:
            try:
                from scaffold.agent.runtime_session import RuntimeSessionStore

                RuntimeSessionStore().request_cancel(runtime_sid)
            except Exception:
                pass
        return True

    def end_run(self, chat_id: str) -> None:
        with self._runs_lock:
            self._active_runs.pop(chat_id, None)
            self._runtime_session_ids.pop(chat_id, None)

    def _reset_usage(self) -> None:
        self._last_cost = 0.0
        self._last_prompt_tokens = 0
        self._last_completion_tokens = 0

    def _metrics_meta(
        self,
        *,
        mode: str,
        model: str,
        message: str = "",
        context_text: str = "",
        ttfb_ms: float = 0.0,
        total_ms: float = 0.0,
        success: bool = True,
        cache_hit: bool = False,
        tokens_saved_est: int = 0,
        handler: str = "qa",
        extra: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        rm = build_run_metrics(
            mode=mode,
            model=model,
            prompt_tokens=self._last_prompt_tokens,
            completion_tokens=self._last_completion_tokens,
            cost_usd=self._last_cost if self._last_cost else None,
            context_text=context_text,
            query_text=message,
            ttfb_ms=ttfb_ms,
            total_ms=total_ms,
            success=success,
            cache_hit=cache_hit,
            tokens_saved_est=tokens_saved_est,
            handler=handler,
        )
        meta = rm.to_meta()
        if extra:
            meta.update(extra)
        return meta

    def _tracker_to_usage(self, tracker: Any) -> None:
        """Copy TokenTracker totals into last-usage fields."""
        prompt = completion = 0
        for records in tracker.requests.values():
            for rec in records:
                prompt += int(rec.get("input", 0))
                completion += int(rec.get("output", 0))
        self._last_prompt_tokens = prompt
        self._last_completion_tokens = completion
        self._last_cost = float(tracker.total_cost)

    # ── Streaming (SSE) ───────────────────────────────────────────────────────

    def handle_message_stream(
        self,
        message: str,
        mode: str = "qa",
        chat_id: Optional[str] = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield SSE event dicts: {event, data}."""
        mode = mode if mode in VALID_MODES else "qa"
        message = (message or "").strip()
        if not message:
            yield {"event": "error", "data": {"error": "empty message"}}
            return

        session, err = self._resolve_session(message, chat_id)
        if err:
            yield {"event": "error", "data": err}
            return

        user_msg = ChatMessage(role="user", content=message, mode=mode)
        session.messages.append(user_msg)

        cancel = self.start_run(session.chat_id)
        cancelled = False

        yield {
            "event": "start",
            "data": {"chat_id": session.chat_id, "mode": mode, "mode_label": MODE_LABELS.get(mode, mode)},
        }

        reply_text = ""
        meta: dict[str, Any] = {}

        try:
            if mode == "qa":
                for evt in self._stream_qa(session, message, cancel):
                    if evt["event"] in ("token", "status", "usage"):
                        yield evt
                    elif evt["event"] == "_complete":
                        reply_text = evt["data"]["text"]
                        meta = evt["data"]["meta"]
                    if cancel.is_set():
                        cancelled = True
                        break
            elif mode == "plan":
                if not cancel.is_set():
                    plan_full, meta = self._run_plan(message)
                    meta["streamed"] = True
                    yield {"event": "usage", "data": meta}
                    yield {"event": "status", "data": {"message": "Plan ready — streaming…"}}
                    streamed: list[str] = []
                    for chunk in _chunk_text(plan_full, size=24):
                        if cancel.is_set():
                            cancelled = True
                            reply_text = "".join(streamed)
                            break
                        streamed.append(chunk)
                        yield {"event": "token", "data": {"content": chunk}}
                        time.sleep(0.012)
                    else:
                        reply_text = plan_full
                else:
                    cancelled = True
            else:
                for evt in self._stream_agent(message, session, cancel):
                    if evt["event"] in ("progress", "token", "status"):
                        yield evt
                    elif evt["event"] == "_complete":
                        reply_text = evt["data"]["text"]
                        meta = evt["data"]["meta"]
                    if cancel.is_set():
                        cancelled = True
                        break
        except Exception as exc:
            if cancel.is_set():
                cancelled = True
            else:
                reply_text = f"Error: {exc}"
                meta = {"success": False, "error": str(exc)}
                yield {"event": "token", "data": {"content": reply_text}}
        finally:
            self.end_run(session.chat_id)

        if cancel.is_set():
            cancelled = True

        if cancelled:
            meta = {**meta, "cancelled": True, "success": False, "partial": True}
            if mode == "agent":
                meta["agent_may_continue"] = True

        if reply_text.strip() or not cancelled:
            assistant_msg = ChatMessage(role="assistant", content=reply_text, mode=mode, meta=meta)
            session.messages.append(assistant_msg)
        if len(session.messages) == 2 and session.title == "New chat":
            session.title = message[:60] + ("…" if len(message) > 60 else "")
        self.store.save(session)

        done_event = "cancelled" if cancelled else "done"
        yield {
            "event": done_event,
            "data": {
                "chat_id": session.chat_id,
                "mode": mode,
                "mode_label": MODE_LABELS.get(mode, mode),
                "reply": reply_text,
                "meta": meta,
                "session": session.to_dict(),
                "cancelled": cancelled,
            },
        }

    def _qa_context_blob(self, session: ChatSession, message: str) -> str:
        parts = [
            self._read_project_context(message),
            self._read_soul_context(),
        ]
        prior = self._conversation_messages(session)
        if prior:
            parts.append("\n".join(f"{m['role']}: {m['content']}" for m in prior))
        return "\n\n".join(p for p in parts if p)

    def _stream_qa(
        self, session: ChatSession, message: str, cancel: threading.Event
    ) -> Iterator[dict[str, Any]]:
        from scaffold.agent.escalation_engine import is_cheap_only

        system, user, cache_key, prior_turns = self._build_qa_prompt(session, message)
        context_blob = self._qa_context_blob(session, message)
        model = "deepseek-chat"
        t0 = time.perf_counter()
        ttfb_ms = 0.0

        cached = self.response_cache.get(cache_key)
        if cached:
            self._reset_usage()
            out_est = _estimate_tokens(cached)
            total_ms = (time.perf_counter() - t0) * 1000.0
            meta = self._metrics_meta(
                mode="qa",
                model="cache",
                message=message,
                context_text=context_blob,
                ttfb_ms=5.0,
                total_ms=total_ms,
                cache_hit=True,
                tokens_saved_est=out_est,
                handler="qa",
                extra={"memory_turns": prior_turns},
            )
            yield {"event": "status", "data": {"message": "Cache hit — instant reply", "cache_hit": True}}
            partial = ""
            for chunk in _chunk_text(cached, size=8):
                if cancel.is_set():
                    if partial:
                        meta = self._metrics_meta(
                            mode="qa",
                            model="cache",
                            message=message,
                            context_text=context_blob,
                            ttfb_ms=5.0,
                            total_ms=(time.perf_counter() - t0) * 1000.0,
                            cache_hit=True,
                            tokens_saved_est=_estimate_tokens(partial),
                            handler="qa",
                            extra={"memory_turns": prior_turns, "cancelled": True, "partial": True},
                        )
                        yield {"event": "_complete", "data": {"text": partial, "meta": meta}}
                    return
                partial += chunk
                yield {"event": "token", "data": {"content": chunk}}
                time.sleep(0.008)
            yield {"event": "usage", "data": meta}
            yield {"event": "_complete", "data": {"text": cached, "meta": meta}}
            return

        self._reset_usage()
        full_parts: list[str] = []
        first_token = True
        if os.getenv("DEEPSEEK_API_KEY") or is_cheap_only():
            for token in self._stream_deepseek(system, user, session):
                if cancel.is_set():
                    break
                if first_token:
                    ttfb_ms = (time.perf_counter() - t0) * 1000.0
                    first_token = False
                full_parts.append(token)
                yield {"event": "token", "data": {"content": token}}
            model = self._last_model
        elif os.getenv("ANTHROPIC_API_KEY"):
            for token in self._stream_anthropic(system, user, session):
                if cancel.is_set():
                    break
                if first_token:
                    ttfb_ms = (time.perf_counter() - t0) * 1000.0
                    first_token = False
                full_parts.append(token)
                yield {"event": "token", "data": {"content": token}}
            model = "claude-haiku-4-5"
            if not self._last_completion_tokens:
                self._last_completion_tokens = _estimate_tokens("".join(full_parts))
        else:
            raise RuntimeError("Set DEEPSEEK_API_KEY or ANTHROPIC_API_KEY in .env")

        if cancel.is_set():
            text = "".join(full_parts)
            if text:
                meta = self._metrics_meta(
                    mode="qa",
                    model=model,
                    message=message,
                    context_text=context_blob,
                    ttfb_ms=ttfb_ms,
                    total_ms=(time.perf_counter() - t0) * 1000.0,
                    handler="qa",
                    extra={"memory_turns": prior_turns, "cancelled": True, "partial": True},
                )
                yield {"event": "_complete", "data": {"text": text, "meta": meta}}
            return

        text = "".join(full_parts)
        total_ms = (time.perf_counter() - t0) * 1000.0
        if not self._last_prompt_tokens and not self._last_completion_tokens:
            msgs = self._llm_messages(system, user, session)
            self._last_prompt_tokens = sum(_estimate_tokens(m["content"]) for m in msgs)
            self._last_completion_tokens = _estimate_tokens(text)

        self.response_cache.set(cache_key, text, model=model)
        meta = self._metrics_meta(
            mode="qa",
            model=model,
            message=message,
            context_text=context_blob,
            ttfb_ms=ttfb_ms,
            total_ms=total_ms,
            handler="qa",
            extra={"memory_turns": prior_turns},
        )
        yield {"event": "usage", "data": meta}
        yield {"event": "_complete", "data": {"text": text, "meta": meta}}

    def _agent_context_blob(self, session: ChatSession) -> str:
        """Prior chat turns for agent runs (Q/A answers, plan context)."""
        prior = self._conversation_messages(session)
        if not prior:
            return ""
        return "\n".join(f"{m['role']}: {m['content']}" for m in prior)

    def _stream_agent(
        self, message: str, session: ChatSession, cancel: threading.Event
    ) -> Iterator[dict[str, Any]]:
        gui_root = Path(self.codebase_root) / ".awos" / "gui"
        known = {p.name for p in gui_root.glob("orch_*")} if gui_root.is_dir() else set()
        result_holder: dict[str, Any] = {}
        error_holder: dict[str, str] = {}

        def _run() -> None:
            try:
                text, meta = self._run_agent(message, session, cancel)
                result_holder["text"] = text
                result_holder["meta"] = meta
            except Exception as exc:
                error_holder["error"] = str(exc)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        agent_thread = thread

        active_sid: Optional[str] = None
        last_event_line = 0
        progress_lines: list[str] = []
        streamed_summary: list[str] = []
        yield {"event": "status", "data": {"message": "Agent started…"}}

        while agent_thread.is_alive() or (active_sid and last_event_line >= 0):
            if cancel.is_set():
                break
            if active_sid is None and gui_root.is_dir():
                candidates = sorted(
                    (p for p in gui_root.glob("orch_*") if p.name not in known),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if candidates:
                    active_sid = candidates[0].name
                    yield {
                        "event": "progress",
                        "data": {"type": "session_start", "session_id": active_sid, "goal": message},
                    }

            if active_sid:
                events_path = gui_root / active_sid / "events.jsonl"
                if events_path.exists():
                    lines = events_path.read_text(encoding="utf-8").splitlines()
                    for line in lines[last_event_line:]:
                        try:
                            evt = json.loads(line)
                            payload = evt.get("payload", evt)
                            label = _format_agent_event(evt.get("type", ""), payload)
                            if label:
                                progress_lines.append(label)
                                yield {"event": "progress", "data": {"type": evt.get("type"), "label": label, "payload": payload}}
                        except json.JSONDecodeError:
                            pass
                    last_event_line = len(lines)

            if not agent_thread.is_alive():
                break
            time.sleep(0.35)

        if cancel.is_set():
            agent_thread.join(timeout=60.0)
            text = result_holder.get("text", "")
            meta = result_holder.get("meta", {})
            meta["memory_turns"] = 0
            meta["streamed"] = True
            meta["cancelled"] = True
            meta["partial"] = True
            meta["agent_may_continue"] = agent_thread.is_alive()
            rs_id = meta.get("runtime_session_id")
            if not rs_id:
                with self._runs_lock:
                    rs_id = self._runtime_session_ids.get(session.chat_id)
                if rs_id:
                    meta["runtime_session_id"] = rs_id
            text = _agent_partial_reply(progress_lines, active_sid, text)
            yield {"event": "_complete", "data": {"text": text, "meta": meta}}
            return

        agent_thread.join(timeout=1.0)

        if error_holder:
            raise RuntimeError(error_holder["error"])

        text = result_holder.get("text", "")
        meta = result_holder.get("meta", {})
        meta["memory_turns"] = 0
        meta["streamed"] = True

        yield {"event": "status", "data": {"message": "Run complete — streaming summary…"}}
        for chunk in _chunk_text(text, size=28):
            if cancel.is_set():
                streamed_summary.append(chunk)
                break
            streamed_summary.append(chunk)
            yield {"event": "token", "data": {"content": chunk}}
            time.sleep(0.01)

        if cancel.is_set() and streamed_summary:
            text = "".join(streamed_summary)
            meta["partial"] = True
            meta["cancelled"] = True

        yield {"event": "_complete", "data": {"text": text, "meta": meta}}

    # ── Non-streaming (legacy POST /api/chat) ─────────────────────────────────

    def handle_message(
        self,
        message: str,
        mode: str = "qa",
        chat_id: Optional[str] = None,
    ) -> dict[str, Any]:
        mode = mode if mode in VALID_MODES else "qa"
        message = (message or "").strip()
        if not message:
            return {"error": "empty message", "status": 400}

        if chat_id:
            session = self.store.load(chat_id)
            if session is None:
                return {"error": f"chat not found: {chat_id}", "status": 404}
        else:
            title = message[:60] + ("…" if len(message) > 60 else "")
            session = self.store.create(title=title)

        user_msg = ChatMessage(role="user", content=message, mode=mode)
        session.messages.append(user_msg)

        try:
            if mode == "qa":
                reply_text, meta = self._run_qa(session, message)
            elif mode == "plan":
                reply_text, meta = self._run_plan(message)
            else:
                reply_text, meta = self._run_agent(message, session)
        except Exception as exc:
            reply_text = f"Error: {exc}"
            meta = {"success": False, "error": str(exc)}

        assistant_msg = ChatMessage(
            role="assistant",
            content=reply_text,
            mode=mode,
            meta=meta,
        )
        session.messages.append(assistant_msg)
        if len(session.messages) == 2 and session.title == "New chat":
            session.title = message[:60] + ("…" if len(message) > 60 else "")
        self.store.save(session)

        return {
            "status": 200,
            "chat_id": session.chat_id,
            "mode": mode,
            "mode_label": MODE_LABELS.get(mode, mode),
            "reply": reply_text,
            "meta": meta,
            "session": session.to_dict(),
        }

    def _resolve_session(
        self, message: str, chat_id: Optional[str]
    ) -> tuple[Optional[ChatSession], Optional[dict[str, Any]]]:
        if chat_id:
            session = self.store.load(chat_id)
            if session is None:
                return None, {"error": f"chat not found: {chat_id}"}
            return session, None
        title = message[:60] + ("…" if len(message) > 60 else "")
        return self.store.create(title=title), None

    def _conversation_messages(self, session: ChatSession, max_turns: int = 12) -> list[dict[str, str]]:
        """Prior turns for LLM context (excludes the message just appended)."""
        prior = session.messages[:-1] if session.messages else []
        msgs: list[dict[str, str]] = []
        for m in prior[-max_turns * 2 :]:
            if m.role in ("user", "assistant") and m.content.strip():
                msgs.append({"role": m.role, "content": m.content[:4000]})
        return msgs

    def _read_soul_context(self, max_chars: int = 2000) -> str:
        """Rules + patterns from SOUL.xml (institutional memory)."""
        soul_path = Path(self.codebase_root) / ".awos" / "SOUL.xml"
        if not soul_path.is_file():
            return ""
        try:
            from scaffold.agent.soul_xml import SoulXML

            soul = SoulXML(soul_path=soul_path)
            learnings = soul.get_learnings()[:12]
            if not learnings:
                raw = soul_path.read_text(encoding="utf-8", errors="ignore")[:max_chars]
                return f"## SOUL.xml (excerpt)\n{raw}" if raw.strip() else ""
            lines = ["## AWOS institutional memory (SOUL)"]
            for ln in learnings:
                lines.append(f"- [{ln.type}] {ln.pattern}" + (f" — {ln.context}" if ln.context else ""))
            return "\n".join(lines)[:max_chars]
        except Exception:
            return ""

    def _build_qa_prompt(
        self, session: ChatSession, message: str
    ) -> tuple[str, str, str, int]:
        """Returns (system, user_prompt, cache_key, prior_turn_count)."""
        context = self._read_project_context(message)
        soul = self._read_soul_context()
        prior = self._conversation_messages(session)
        prior_turns = len([m for m in prior if m["role"] == "user"])

        system = (
            "You are AWOS assistant. AWOS is an Autonomous Work Operating System — "
            "a persistent execution kernel optimizing quality × speed ÷ cost. "
            "Models are replaceable; intelligence compounds. Coding is App #1. "
            "Answer clearly in prose. Use conversation history when relevant. "
            "Do not propose file edits unless explicitly asked."
        )
        blocks = [f"Project context:\n{context}"]
        if soul:
            blocks.append(soul)
        blocks.append(f"Question: {message}")
        user = "\n\n".join(blocks)

        hist_key = "|".join(f"{m['role']}:{m['content'][:80]}" for m in prior[-6:])
        cache_key = self.response_cache.make_key(
            "gui-qa",
            f"{prior_turns}::{hist_key}::{message}",
        )
        return system, user, cache_key, prior_turns

    def _read_project_context(self, query: str = "", max_chars: int = 12000) -> str:
        """Load canonical docs + keyword-matched source snippets."""
        root = Path(self.codebase_root)
        parts: list[str] = []

        for rel in (
            "VISION.md",
            "memories/repo/project_structure.md",
            "README.md",
            "docs/specs/gui_layer_spec.md",
        ):
            path = root / rel
            if path.is_file():
                text = path.read_text(encoding="utf-8", errors="ignore")[:4000]
                parts.append(f"## {rel}\n{text}")

        keywords = [w.lower() for w in query.split() if len(w) > 3][:8]
        py_root = root / "scaffold" / "agent"
        if py_root.is_dir() and keywords:
            scored: list[tuple[int, Path]] = []
            for py in py_root.glob("*.py"):
                name_score = sum(1 for k in keywords if k in py.stem.lower())
                if name_score:
                    scored.append((name_score, py))
            scored.sort(reverse=True)
            for _score, py in scored[:3]:
                snippet = py.read_text(encoding="utf-8", errors="ignore")[:1200]
                parts.append(f"## {py.relative_to(root)}\n{snippet}")

        blob = "\n\n".join(parts)
        return blob[:max_chars] if blob else "(no project context found)"

    def _llm_messages(self, system: str, user: str, session: ChatSession) -> list[dict[str, str]]:
        msgs: list[dict[str, str]] = [{"role": "system", "content": system}]
        msgs.extend(self._conversation_messages(session))
        msgs.append({"role": "user", "content": user})
        return msgs

    def _stream_deepseek(
        self, system: str, user: str, session: ChatSession, max_tokens: int = 2048
    ) -> Iterator[str]:
        from openai import OpenAI

        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY not set in .env")

        client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
        stream = client.chat.completions.create(
            model="deepseek-chat",
            messages=self._llm_messages(system, user, session),
            max_tokens=max_tokens,
            temperature=0.3,
            stream=True,
            stream_options={"include_usage": True},
        )
        self._last_model = "deepseek-chat"
        for chunk in stream:
            if chunk.choices:
                delta = chunk.choices[0].delta.content or ""
                if delta:
                    yield delta
            if chunk.usage:
                u = chunk.usage
                self._last_prompt_tokens = int(u.prompt_tokens or 0)
                self._last_completion_tokens = int(u.completion_tokens or 0)
                self._last_cost = (self._last_prompt_tokens / 1_000_000) * 0.14 + (
                    self._last_completion_tokens / 1_000_000
                ) * 0.28

    def _stream_anthropic(
        self, system: str, user: str, session: ChatSession, max_tokens: int = 2048
    ) -> Iterator[str]:
        from anthropic import Anthropic

        client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        prior = self._conversation_messages(session)
        api_messages = list(prior) + [{"role": "user", "content": user}]
        with client.messages.stream(
            model="claude-haiku-4-5",
            max_tokens=max_tokens,
            system=system,
            messages=api_messages,
        ) as stream:
            for text in stream.text_stream:
                if text:
                    yield text
            final = stream.get_final_message()
            if final.usage:
                self._last_prompt_tokens = int(final.usage.input_tokens or 0)
                self._last_completion_tokens = int(final.usage.output_tokens or 0)
                self._last_cost = (
                    self._last_prompt_tokens * 0.80 + self._last_completion_tokens * 4.00
                ) / 1_000_000

    def _call_deepseek(
        self, system: str, user: str, session: Optional[ChatSession] = None, max_tokens: int = 2048
    ) -> str:
        if session is None:
            session = ChatSession(chat_id="ephemeral")
        return "".join(self._stream_deepseek(system, user, session, max_tokens=max_tokens))

    def _budget_spent(self) -> float:
        try:
            from scaffold.agent.budget_ledger import get_ledger
            return float(get_ledger().get_status().get("spent", 0.0))
        except Exception:
            return 0.0

    def _run_qa(self, session: ChatSession, message: str) -> tuple[str, dict[str, Any]]:
        from scaffold.agent.escalation_engine import is_cheap_only

        system, user, cache_key, prior_turns = self._build_qa_prompt(session, message)
        context_blob = self._qa_context_blob(session, message)
        cached = self.response_cache.get(cache_key)
        if cached:
            self._reset_usage()
            return cached, self._metrics_meta(
                mode="qa",
                model="cache",
                message=message,
                context_text=context_blob,
                cache_hit=True,
                tokens_saved_est=_estimate_tokens(cached),
                handler="qa",
                extra={"memory_turns": prior_turns},
            )

        t0 = time.perf_counter()
        self._reset_usage()
        if os.getenv("DEEPSEEK_API_KEY") or is_cheap_only():
            text = self._call_deepseek(system, user, session=session)
            model = self._last_model
        elif os.getenv("ANTHROPIC_API_KEY"):
            text = "".join(self._stream_anthropic(system, user, session))
            model = "claude-haiku-4-5"
        else:
            raise RuntimeError("Set DEEPSEEK_API_KEY or ANTHROPIC_API_KEY in .env")

        if not self._last_prompt_tokens and not self._last_completion_tokens:
            msgs = self._llm_messages(system, user, session)
            self._last_prompt_tokens = sum(_estimate_tokens(m["content"]) for m in msgs)
            self._last_completion_tokens = _estimate_tokens(text)

        self.response_cache.set(cache_key, text, model=model)
        total_ms = (time.perf_counter() - t0) * 1000.0
        return text, self._metrics_meta(
            mode="qa",
            model=model,
            message=message,
            context_text=context_blob,
            total_ms=total_ms,
            handler="qa",
            extra={"memory_turns": prior_turns},
        )

    def _run_plan(self, message: str) -> tuple[str, dict[str, Any]]:
        from scaffold.agent.cheap_planner import CheapPlanner
        from scaffold.agent.orchestrator import Orchestrator
        from scaffold.agent.token_tracker import TokenTracker

        t0 = time.perf_counter()
        tracker = TokenTracker(monthly_budget=float(os.getenv("AWOS_MONTHLY_BUDGET", "20.0")))
        orch = Orchestrator(tracker=tracker)
        ctx = orch._discover_codebase_context(self.codebase_root)
        planner = CheapPlanner()
        plan = planner.plan(message, ctx, tracker=tracker)

        tasks = plan.get("plan", [])
        reasoning = plan.get("reasoning", "")
        lines = [f"**Plan** — {len(tasks)} task(s)\n"]
        if reasoning:
            lines.append(f"{reasoning}\n")
        for t in tasks:
            lines.append(
                f"- **Task {t.get('task_id')}** `{t.get('file', '?')}` "
                f"({t.get('complexity', '?')}): {t.get('action', '')}"
            )
        lines.append("\n_Switch to **Agent** mode to execute this plan._")

        self._tracker_to_usage(tracker)
        reply = "\n".join(lines)
        total_ms = (time.perf_counter() - t0) * 1000.0
        return reply, self._metrics_meta(
            mode="plan",
            model="planner",
            message=message,
            context_text=ctx[:8000] if isinstance(ctx, str) else str(ctx)[:8000],
            total_ms=total_ms,
            handler="plan",
            extra={
                "tasks": tasks,
                "reasoning": reasoning,
                "total_tasks": len(tasks),
            },
        )

    def _run_agent(
        self,
        message: str,
        session: Optional[ChatSession] = None,
        cancel: Optional[threading.Event] = None,
    ) -> tuple[str, dict[str, Any]]:
        from scaffold.agent.orchestrator import Orchestrator
        from scaffold.agent.token_tracker import TokenTracker

        try:
            from scaffold.agent.learning_policy import apply_kernel_defaults
            apply_kernel_defaults()
        except ImportError:
            from learning_policy import apply_kernel_defaults
            apply_kernel_defaults()
        budget_before = self._budget_spent()
        tracker = TokenTracker(monthly_budget=float(os.getenv("AWOS_MONTHLY_BUDGET", "20.0")))
        orch = Orchestrator(tracker=tracker)
        extra = self._agent_context_blob(session) if session else ""

        def _on_runtime_session(rs_id: str) -> None:
            if session is None:
                return
            with self._runs_lock:
                self._runtime_session_ids[session.chat_id] = rs_id

        result = orch.execute_feature(
            goal=message,
            codebase_root=self.codebase_root,
            extra_context=extra,
            cancel_event=cancel,
            on_runtime_session=_on_runtime_session,
        )

        run_id = result.get("session_id", "")
        runtime_session_id = result.get("runtime_session_id")
        success = bool(result.get("success"))
        completed = int(result.get("tasks_completed", 0))
        total = int(result.get("total_tasks", completed))
        elapsed = float(result.get("time_elapsed", 0))

        manifest_files: list[dict[str, Any]] = []
        if run_id:
            manifest_path = Path(self.codebase_root) / ".awos" / "gui" / run_id / "manifest.json"
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest_files = manifest.get("files", [])

        if success:
            text = f"**Agent run complete** — {completed}/{total} task(s) in {elapsed:.1f}s.\n\n"
        else:
            text = f"**Agent run finished with issues** — {completed}/{total} succeeded.\n\n"
            errors = result.get("errors") or []
            if errors:
                text += "Errors:\n" + "\n".join(f"- {e}" for e in errors[:3]) + "\n\n"

        if manifest_files:
            text += "**Files changed:**\n"
            for f in manifest_files:
                text += (
                    f"- `{f.get('path')}` ({f.get('status')}) "
                    f"+{f.get('lines_added', 0)} -{f.get('lines_removed', 0)}\n"
                )
            text += f"\n[View diffs in Runs tab →](?view=runs&session={run_id})"
        elif result.get("deliverable_paths"):
            text += "**Files created:**\n"
            for p in result["deliverable_paths"]:
                text += f"- `{p}` (created)\n"
            if run_id:
                text += f"\n[View in Runs tab →](?view=runs&session={run_id})"
        elif run_id:
            text += f"\nRun ID: `{run_id}` — open **Runs** tab to inspect."

        cost = max(0.0, self._budget_spent() - budget_before)
        self._tracker_to_usage(tracker)
        self._last_cost = cost

        return text, self._metrics_meta(
            mode="agent",
            model="orchestrator",
            message=message,
            total_ms=elapsed * 1000.0,
            success=success,
            handler="agent",
            extra={
                "run_session_id": run_id,
                "runtime_session_id": runtime_session_id,
                "tasks_completed": completed,
                "tasks_failed": int(result.get("tasks_failed", 0)),
                "total_tasks": total,
                "elapsed_sec": elapsed,
                "files": manifest_files,
            },
        )
