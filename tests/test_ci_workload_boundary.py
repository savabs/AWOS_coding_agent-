"""Regression tests for workload-neutral session interfaces."""

from types import SimpleNamespace
from unittest.mock import patch
import inspect

from gui.server import GuiHandler


def _session(session_id: str, completed: list[str], failed: list[str], spent: float):
    return SimpleNamespace(
        session_id=session_id,
        goal="Improve the coding agent",
        created_at="2026-07-27T10:00:00",
        updated_at="2026-07-27T10:05:00",
        status=SimpleNamespace(value="completed"),
        progress=SimpleNamespace(
            completed_task_ids=completed,
            failed_task_ids=failed,
        ),
        budget={"spent_usd": spent},
    )


def _handler() -> GuiHandler:
    return GuiHandler.__new__(GuiHandler)


def test_generic_session_stats_are_workload_neutral():
    sessions = [_session("rs_one", ["one", "two"], [], 0.25)]
    with patch("scaffold.agent.runtime_session.RuntimeSessionStore") as store_cls:
        store_cls.return_value.list_sessions.return_value = sessions
        stats = _handler()._get_session_stats()

    assert stats["total_runs"] == 1
    assert stats["total_tasks_completed"] == 2
    assert stats["total_cost_usd"] == 0.25
    assert stats["total_rescues"] == stats["total_runs"]
    assert stats["total_tests_fixed"] == stats["total_tasks_completed"]


def test_generic_session_history_preserves_session_contract():
    sessions = [_session("rs_one", ["one"], ["two"], 0.125)]
    with patch("scaffold.agent.runtime_session.RuntimeSessionStore") as store_cls:
        store_cls.return_value.list_sessions.return_value = sessions
        history = _handler()._get_session_history()

    assert history == [{
        "session_id": "rs_one",
        "goal": "Improve the coding agent",
        "timestamp": "2026-07-27T10:05:00",
        "status": "completed",
        "success": True,
        "tasks_completed": 1,
        "tasks_failed": 1,
        "total_cost_usd": 0.125,
        "tests_fixed": 1,
    }]


def test_generic_routes_and_ci_aliases_share_handler_path():
    source = inspect.getsource(GuiHandler.do_GET)
    assert '"/api/sessions/stats"' in source
    assert '"/api/sessions/history"' in source
    assert '"/api/ci-rescue/stats"' in source
    assert '"/api/ci-rescue/history"' in source
