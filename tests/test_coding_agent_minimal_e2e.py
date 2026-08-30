"""Minimal zero-API proof of the AWOS coding-agent loop.

Governing task: tasks/active/coding_agent_minimal_e2e.html
Governing spec: docs/specs/coding_agent_minimal_e2e_spec.html

The planner and worker model boundaries are deterministic mocks. The real
Orchestrator, Verifier, file I/O, TestRunner, and debug journal remain active.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def isolated_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a tiny isolated Python repository for one coding task."""
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
    monkeypatch.setenv("AWOS_REACT_WORKER", "0")
    monkeypatch.setenv("AWOS_RUNTIME_SESSION", "0")
    monkeypatch.setenv("AWOS_LEARNING_DISABLE", "1")
    monkeypatch.setenv("AWOS_MCTS_DISABLE", "1")
    # Keep planner selection deterministic even when the developer shell has
    # cheap-only routing enabled. The test patches the advanced Planner below;
    # it must never fall through to a real provider request.
    monkeypatch.delenv("AWOS_CHEAP_ONLY", raising=False)
    monkeypatch.delenv("AWOS_PREMIUM_BUDGET", raising=False)
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "fake-key-for-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-advanced-planner-key")
    return repo


def test_plan_edit_verify_recover_and_test(isolated_repo: Path) -> None:
    """Prove plan → bad patch → retry → verify → pytest → success."""
    from scaffold.agent.orchestrator import Orchestrator

    plan = {
        "plan": [
            {
                "task_id": 1,
                "task_type": "edit_file",
                "file": "calculator.py",
                "path": "calculator.py",
                "action": "Add add(a, b) that returns the sum of two numbers",
                "complexity": "low",
                "function_signature": "def add(a, b)",
            }
        ],
        "reasoning": "One isolated function change",
        "total_tasks": 1,
    }
    bad_patch = {
        "success": True,
        "search": "# TODO\n",
        "replace": "def add(a, b):\n    return (a + b\n",
        "reasoning": "First attempt intentionally contains a syntax error",
    }
    good_patch = {
        "success": True,
        "search": "# TODO\n",
        "replace": "def add(a, b):\n    return a + b\n",
        "reasoning": "Corrected the syntax after verifier feedback",
    }

    with (
        patch("scaffold.agent.orchestrator.Planner.plan", return_value=plan) as plan_mock,
        patch(
            "scaffold.agent.orchestrator.Worker.execute_task",
            side_effect=[bad_patch, good_patch],
        ) as worker_mock,
        patch.object(Orchestrator, "_cheap_call", return_value=""),
        patch(
            "scaffold.agent.core.performance_tracker.ToolPerformanceTracker._load",
            return_value=None,
        ),
    ):
        orchestrator = Orchestrator()
        result = orchestrator.execute_feature(
            goal="Add add(a, b) to calculator.py",
            codebase_root=str(isolated_repo),
        )

    plan_mock.assert_called_once()
    assert worker_mock.call_count == 2, "the verifier should force one worker retry"
    assert result["success"] is True, result.get("errors")
    assert result["tasks_completed"] == 1
    assert result["tasks_failed"] == 0

    source = (isolated_repo / "calculator.py").read_text(encoding="utf-8")
    assert source == "def add(a, b):\n    return a + b\n"

    debug_session_id = result.get("debug_session_id")
    assert debug_session_id
    debug_file = isolated_repo / ".awos" / "debug" / debug_session_id / "evidence.jsonl"
    assert debug_file.exists()
    evidence = [
        json.loads(line)
        for line in debug_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    test_results = [
        item
        for item in evidence
        if item.get("payload", {}).get("record_type") == "test_result"
    ]
    assert test_results, "the real TestRunner result was not recorded"
    assert test_results[-1]["payload"]["passed"] is True
    assert any(
        item.get("payload", {}).get("normalized_event_type") == "session_done"
        for item in evidence
    )
