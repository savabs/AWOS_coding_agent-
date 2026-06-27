"""Multi-step pause/resume through Orchestrator + RuntimeSession."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from scaffold.agent.orchestrator import Orchestrator
from scaffold.agent.runtime_session import RuntimeSessionStore, SessionStatus


@pytest.fixture
def sessions_dir(tmp_path, monkeypatch):
    d = tmp_path / ".awos" / "sessions"
    d.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AWOS_RUNTIME_SESSION", "true")
    monkeypatch.setenv("AWOS_LEARNING_DISABLE", "true")
    monkeypatch.setenv("AWOS_MCTS_DISABLE", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-testing")
    monkeypatch.setenv("AWOS_E2E", "1")
    return d


def _three_task_plan():
    return [
        {
            "task_id": 1,
            "task_type": "edit_file",
            "path": "module_a.py",
            "file": "module_a.py",
            "action": "Edit module_a",
            "complexity": "low",
        },
        {
            "task_id": 2,
            "task_type": "edit_file",
            "path": "module_b.py",
            "file": "module_b.py",
            "action": "Edit module_b",
            "complexity": "low",
        },
        {
            "task_id": 3,
            "task_type": "edit_file",
            "path": "module_c.py",
            "file": "module_c.py",
            "action": "Edit module_c",
            "complexity": "low",
        },
    ]


def _make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for name in ("module_a.py", "module_b.py", "module_c.py"):
        (root / name).write_text(f"# {name}\n", encoding="utf-8")
    return root


class TestPauseResumeMultiStep:
    def test_pause_after_first_task_resumes_remaining(self, tmp_path, sessions_dir):
        repo = _make_repo(tmp_path)
        plan = _three_task_plan()
        goal = "stage1 pause-resume proof"
        orch = Orchestrator()
        executed: list = []

        def mock_execute(task, ctx):
            executed.append(task["task_id"])
            if len(executed) == 1:
                orch._pause_requested = True
            return {"task_id": task["task_id"], "success": True, "task": task}

        with patch.object(orch, "_execute_single_task", side_effect=mock_execute), \
             patch("scaffold.agent.core.performance_tracker.ToolPerformanceTracker._load"):
            first = orch.execute_feature(
                goal=goal,
                codebase_root=str(repo),
                pre_planned_tasks=plan,
            )

        rs_id = first["runtime_session_id"]
        assert rs_id is not None
        store = RuntimeSessionStore(sessions_dir=str(sessions_dir))
        paused = store.load(rs_id)
        assert paused.status == SessionStatus.PAUSED
        assert paused.progress.completed_task_ids == [1]
        assert paused.progress.total_tasks == 3
        assert executed == [1]

        executed.clear()
        orch2 = Orchestrator()

        def mock_execute_resume(task, ctx):
            executed.append(task["task_id"])
            return {"task_id": task["task_id"], "success": True, "task": task}

        with patch.object(orch2, "_execute_single_task", side_effect=mock_execute_resume), \
             patch("scaffold.agent.core.performance_tracker.ToolPerformanceTracker._load"):
            second = orch2.execute_feature(
                goal=goal,
                codebase_root=str(repo),
                pre_planned_tasks=plan,
                session_id=rs_id,
                resume=True,
            )

        completed = store.load(rs_id)
        assert completed.status == SessionStatus.COMPLETED
        assert completed.progress.completed_task_ids == [1, 2, 3]
        assert executed == [2, 3]
        assert second["success"] is True
