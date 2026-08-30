"""Regression tests for the test outcome gate.

Governing task: tasks/active/test_outcome_gate.html
Governing spec: docs/specs/test_outcome_gate_spec.html

Model boundaries are deterministic mocks. The real orchestrator, verifier/file
application, TestRunner, GitManager rollback, reward bookkeeping, and debug
journal remain active.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def isolated_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calculator.py").write_text("# TODO\n", encoding="utf-8")
    (repo / "test_calculator.py").write_text(
        "from calculator import add\n\n\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    (repo / "pytest.ini").write_text(
        "[pytest]\n"
        "testpaths = test_calculator.py\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)
    monkeypatch.setenv("AWOS_SAFE_TO_RUN_TESTS", "1")
    monkeypatch.setenv("AWOS_RUNTIME_SESSION", "0")
    monkeypatch.setenv("AWOS_REACT_WORKER", "0")
    monkeypatch.setenv("AWOS_LEARNING_DISABLE", "1")
    monkeypatch.setenv("AWOS_MCTS_DISABLE", "1")
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "fake-key-for-test")
    return repo


def _plan() -> list[dict[str, object]]:
    return [
        {
            "task_id": 1,
            "task_type": "edit_file",
            "file": "calculator.py",
            "path": "calculator.py",
            "action": "Add add(a, b) that returns the sum of two numbers",
            "complexity": "low",
        }
    ]


def _wrong_patch() -> dict[str, object]:
    return {
        "success": True,
        "search": "# TODO\n",
        "replace": "def add(a, b):\n    return a - b\n",
        "reasoning": "syntactically valid but behaviorally wrong",
    }


def _right_patch() -> dict[str, object]:
    return {
        "success": True,
        "search": "# TODO\n",
        "replace": "def add(a, b):\n    return a + b\n",
        "reasoning": "correct implementation",
    }


def _run_legacy(repo: Path, worker_result: dict[str, object]):
    from scaffold.agent.orchestrator import Orchestrator

    with (
        patch("scaffold.agent.orchestrator.Worker.execute_task", return_value=worker_result),
        patch.object(Orchestrator, "_cheap_call", return_value=""),
        patch.object(Orchestrator, "_check_stagnation", return_value=False),
        patch(
            "scaffold.agent.core.performance_tracker.ToolPerformanceTracker._load",
            return_value=None,
        ),
    ):
        orchestrator = Orchestrator()
        return orchestrator.execute_feature(
            goal="Add add(a, b) to calculator.py",
            codebase_root=str(repo),
            pre_planned_tasks=_plan(),
        )


def test_legacy_test_failure_rolls_back_and_reports_failure(isolated_repo: Path) -> None:
    """A valid edit that fails pytest must not be reported as completed."""
    from scaffold.agent.orchestrator import Orchestrator
    from scaffold.agent.task_decomposer import TaskDecomposer

    with (
        patch.object(
            Orchestrator,
            "_replan_after_verify_fail",
            side_effect=AssertionError("test failures must not trigger replanning"),
        ),
        patch.object(
            TaskDecomposer,
            "decompose",
            side_effect=AssertionError("test failures must not trigger decomposition"),
        ),
    ):
        result = _run_legacy(isolated_repo, _wrong_patch())

    assert result["success"] is False
    assert result["tasks_completed"] == 0
    assert result["tasks_failed"] >= 1
    assert result["execution_log"]
    assert all(log["status"] == "failed" for log in result["execution_log"])
    assert (isolated_repo / "calculator.py").read_text(encoding="utf-8") == "# TODO\n"


def test_react_test_failure_rolls_back_and_reports_failure(
    isolated_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ReAct test failure must use failed bookkeeping and restore every edit."""
    monkeypatch.setenv("AWOS_REACT_WORKER", "1")
    from scaffold.agent.orchestrator import Orchestrator
    from scaffold.agent.test_runner import TestResult

    target = isolated_repo / "calculator.py"

    def execute_react_task(**_kwargs):
        target.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        return {
            "success": True,
            "summary": "applied wrong implementation",
            "files_changed": ["calculator.py"],
            "test_result": TestResult(
                passed=0,
                failed=1,
                pass_rate=0.0,
                raw_output="assert 5 == 1",
            ),
        }

    with (
        patch("scaffold.agent.orchestrator.ReActWorker.execute_task", side_effect=execute_react_task),
        patch.object(Orchestrator, "_cheap_call", return_value=""),
        patch.object(Orchestrator, "_check_stagnation", return_value=False),
        patch(
            "scaffold.agent.core.performance_tracker.ToolPerformanceTracker._load",
            return_value=None,
        ),
    ):
        orchestrator = Orchestrator()
        result = orchestrator.execute_feature(
            goal="Add add(a, b) to calculator.py",
            codebase_root=str(isolated_repo),
            pre_planned_tasks=_plan(),
        )

    assert result["success"] is False
    assert result["tasks_completed"] == 0
    assert result["tasks_failed"] >= 1
    assert result["execution_log"]
    assert all(log["status"] == "failed" for log in result["execution_log"])
    assert target.read_text(encoding="utf-8") == "# TODO\n"


def test_passing_test_keeps_edit_and_reports_success(isolated_repo: Path) -> None:
    """A fully passing legacy task retains its edit and completed status."""
    result = _run_legacy(isolated_repo, _right_patch())

    assert result["success"] is True
    assert result["tasks_completed"] == 1
    assert result["tasks_failed"] == 0
    assert result["execution_log"][-1]["status"] == "completed"
    assert "return a + b" in (isolated_repo / "calculator.py").read_text(encoding="utf-8")
