"""Tests for GuiChatStore persistence."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from scaffold.agent.gui_chat import GuiChatService, GuiChatStore


@pytest.fixture
def store(tmp_path: Path) -> GuiChatStore:
    return GuiChatStore(tmp_path)


def test_delete_removes_session(store: GuiChatStore) -> None:
    session = store.create(title="To delete")
    assert store.load(session.chat_id) is not None

    assert store.delete(session.chat_id) is True
    assert store.load(session.chat_id) is None
    assert store.delete(session.chat_id) is False


def test_delete_all_removes_every_session(store: GuiChatStore) -> None:
    store.create(title="One")
    store.create(title="Two")
    assert len(store.list_sessions()) == 2

    deleted = store.delete_all()
    assert deleted == 2
    assert store.list_sessions() == []


def test_stop_run_signals_cancel(tmp_path: Path) -> None:
    store = GuiChatStore(tmp_path)
    svc = GuiChatService(codebase_root=str(tmp_path), store=store)
    session = store.create(title="Active")

    cancel = svc.start_run(session.chat_id)
    assert not cancel.is_set()
    assert svc.stop_run(session.chat_id) is True
    assert cancel.is_set()
    assert svc.stop_run(session.chat_id) is True
    assert svc.stop_run("chat_missing") is False

    svc.end_run(session.chat_id)
    assert svc.stop_run(session.chat_id) is False


def test_list_sessions_after_delete(store: GuiChatStore) -> None:
    keep = store.create(title="Keep")
    gone = store.create(title="Gone")
    store.delete(gone.chat_id)

    sessions = store.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["chat_id"] == keep.chat_id


def test_agent_partial_reply_uses_progress() -> None:
    from scaffold.agent.gui_chat import _agent_partial_reply

    text = _agent_partial_reply(
        ["Planning done — 2 task(s)", "Worker running on `foo.py`"],
        "orch_abc",
    )
    assert "Planning done" in text
    assert "foo.py" in text
    assert "orch_abc" in text


def test_agent_partial_reply_keeps_existing_text() -> None:
    from scaffold.agent.gui_chat import _agent_partial_reply

    text = _agent_partial_reply(["ignored"], "orch_abc", partial_text="Partial answer here.")
    assert text == "Partial answer here."


def test_stop_run_requests_runtime_session_cancel(tmp_path: Path, monkeypatch) -> None:
    store = GuiChatStore(tmp_path)
    svc = GuiChatService(codebase_root=str(tmp_path), store=store)
    session = store.create(title="Active")
    cancel = svc.start_run(session.chat_id)
    rs_id = "rs_" + "a" * 12
    with svc._runs_lock:
        svc._runtime_session_ids[session.chat_id] = rs_id

    cancelled: list[str] = []

    class FakeStore:
        def request_cancel(self, sid: str) -> None:
            cancelled.append(sid)

    monkeypatch.setattr(
        "scaffold.agent.runtime_session.RuntimeSessionStore",
        lambda: FakeStore(),
    )

    assert svc.stop_run(session.chat_id) is True
    assert cancel.is_set()
    assert cancelled == [rs_id]


def test_run_agent_passes_cancel_to_orchestrator(tmp_path: Path, monkeypatch) -> None:
    captured: dict = {}

    class FakeOrch:
        def __init__(self, tracker=None) -> None:
            pass

        def execute_feature(self, **kwargs):
            captured.update(kwargs)
            return {
                "success": True,
                "session_id": "orch_test",
                "runtime_session_id": "rs_" + "b" * 12,
                "tasks_completed": 0,
                "tasks_failed": 0,
                "total_tasks": 0,
                "time_elapsed": 0.1,
            }

    monkeypatch.setattr("scaffold.agent.orchestrator.Orchestrator", FakeOrch)
    monkeypatch.setattr(
        "scaffold.agent.token_tracker.TokenTracker",
        lambda monthly_budget=20.0: object(),
    )
    monkeypatch.setattr(
        "scaffold.agent.gui_chat.GuiChatService._budget_spent",
        lambda self: 0.0,
    )
    monkeypatch.setattr(
        "scaffold.agent.gui_chat.GuiChatService._tracker_to_usage",
        lambda self, tracker: None,
    )

    svc = GuiChatService(codebase_root=str(tmp_path))
    ev = threading.Event()
    _text, meta = svc._run_agent("build feature", cancel=ev)

    assert captured.get("cancel_event") is ev
    assert captured.get("goal") == "build feature"
    assert meta.get("runtime_session_id") == "rs_" + "b" * 12
