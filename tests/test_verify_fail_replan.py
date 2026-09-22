"""Verify-fail → replan: verifier rejects patch, orchestrator revises task and retries."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from scaffold.agent.orchestrator import Orchestrator
from scaffold.agent.plan_actions import replan_task_after_verify_fail


@pytest.fixture(autouse=True)
def _e2e_env(monkeypatch):
    monkeypatch.setenv("AWOS_E2E", "1")
    monkeypatch.setenv("AWOS_MCTS_DISABLE", "true")
    monkeypatch.setenv("AWOS_LEARNING_DISABLE", "true")


def test_replan_task_adds_fidelity_hint():
    task = {
        "task_id": 1,
        "file": "worker.py",
        "action": "Add comment above `_try_cheap_fallback`",
        "complexity": "low",
    }
    revised = replan_task_after_verify_fail(
        task,
        "fidelity_fail: task asked for comment above but patch added docstring",
    )
    assert len(revised) == 1
    assert revised[0]["task_id"] == "1_r1"
    assert "# hash comment" in revised[0]["action"]
    assert revised[0]["_replan_attempted"] is True


def test_orchestrator_replan_after_verify_fail(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    py = tmp_path / "worker.py"
    py.write_text(
        "class W:\n"
        "    def _try_cheap_fallback(self, x):\n"
        "        return x\n",
        encoding="utf-8",
    )

    bad_search = "    def _try_cheap_fallback(self, x):\n        return x\n"
    bad_replace = (
        '    def _try_cheap_fallback(self, x):\n'
        '        """Uses alternate cheap providers."""\n'
        "        return x\n"
    )
    good_search = "    def _try_cheap_fallback(self, x):\n"
    good_replace = (
        "    # Tries alternate cheap providers when primary fails.\n"
        "    def _try_cheap_fallback(self, x):\n"
    )

    calls = {"n": 0}

    def mock_worker(task, file_content, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "success": True,
                "search": bad_search,
                "replace": bad_replace,
                "reasoning": "bad docstring attempt",
            }
        return {
            "success": True,
            "search": good_search,
            "replace": good_replace,
            "reasoning": "comment above function",
        }

    plan = [
        {
            "task_id": 1,
            "task_type": "edit_file",
            "path": "worker.py",
            "file": "worker.py",
            "action": "Add comment above `_try_cheap_fallback` explaining cheap providers",
            "complexity": "low",
        }
    ]

    orch = Orchestrator()
    with patch.object(orch.worker, "execute_task", side_effect=mock_worker), patch(
        "scaffold.agent.core.performance_tracker.ToolPerformanceTracker._load"
    ), patch.object(orch, "_cheap_call", return_value=""), patch.object(
        orch.post_mortem, "reflect", return_value=None
    ):
        result = orch.execute_feature(
            goal="Add comment above _try_cheap_fallback in worker.py",
            codebase_root=str(tmp_path),
            pre_planned_tasks=plan,
        )

    assert result["success"] is True
    assert result["tasks_completed"] == 1
    assert calls["n"] == 2
    text = py.read_text(encoding="utf-8")
    assert "# Tries alternate cheap providers" in text
    assert '"""Uses alternate cheap providers"""' not in text
