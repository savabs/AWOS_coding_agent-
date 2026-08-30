"""Provider-free tests for Planner-native orchestration."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from scaffold.agent.execution_graph import ExecutionNodeStatus
from scaffold.agent.orchestrator import Orchestrator


def _node(node_id: str, node_type: str, **overrides) -> dict:
    node = {
        "node_id": node_id,
        "type": node_type,
        "description": f"Perform {node_id}",
        "depends_on": [],
        "input_files": [],
        "output_files": [],
        "acceptance_check": None,
        "evidence_ref": None,
        "parallel_safe": True,
        "retry_limit": 0,
        "complexity": "low",
    }
    node.update(overrides)
    return node


def _graph(*nodes: dict) -> dict:
    return {"graph_version": "1", "nodes": list(nodes)}


def _orchestrator() -> Orchestrator:
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.execution_log = []
    orchestrator.integration_reviewer = Mock(return_value={"passed": True})
    return orchestrator


def test_runtime_restores_and_validates_native_graph() -> None:
    orchestrator = _orchestrator()
    graph = orchestrator._restore_native_execution_graph(
        _graph(
            _node("explore", "explore"),
            _node(
                "edit", "edit", depends_on=["explore"],
                output_files=["src/app.py"],
                acceptance_check="pytest tests/test_app.py",
            ),
        )
    )

    assert list(graph.nodes) == ["explore", "edit"]
    assert graph.get_node("edit").read_only is False
    assert graph.get_node("explore").read_only is True


def test_runtime_rejects_native_graph_before_execution() -> None:
    orchestrator = _orchestrator()
    with pytest.raises(ValueError, match="unknown planner dependencies"):
        orchestrator._restore_native_execution_graph(
            _graph(_node("edit", "edit", depends_on=["missing"], output_files=["app.py"]))
        )


def test_native_batch_dispatches_edit_test_and_barrier_nodes(tmp_path) -> None:
    orchestrator = _orchestrator()
    orchestrator._execute_single_task = Mock(
        return_value={"task_id": "edit", "success": True, "task": {}}
    )
    graph = orchestrator._restore_native_execution_graph(
        _graph(
            _node("edit", "edit", output_files=["src/app.py"]),
            _node("barrier", "barrier", depends_on=["edit"], parallel_safe=False),
            _node("tests", "test", depends_on=["barrier"], acceptance_check="pytest"),
        )
    )
    test_result = SimpleNamespace(
        no_tests_found=False, timed_out=False, failed=0, errors=0, pass_rate=1.0
    )

    with patch("scaffold.agent.orchestrator.TestRunner") as runner_cls:
        runner_cls.return_value.run.return_value = test_result
        results = orchestrator._run_native_graph_batch(
            graph, {"codebase_root": str(tmp_path), "exploration": {}}
        )

    assert [item["node_id"] for item in results] == ["edit", "barrier", "tests"]
    assert all(item["success"] for item in results)
    orchestrator._execute_single_task.assert_called_once()
    runner_cls.return_value.run.assert_called_once_with(changed_files=[])
    assert graph.get_node("tests").status == ExecutionNodeStatus.COMPLETED


def test_failed_native_test_does_not_release_final_verify(tmp_path) -> None:
    orchestrator = _orchestrator()
    graph = orchestrator._restore_native_execution_graph(
        _graph(
            _node("tests", "test", acceptance_check="pytest"),
            _node("final", "final_verify", depends_on=["tests"], parallel_safe=False),
        )
    )
    failed_result = SimpleNamespace(
        no_tests_found=False, timed_out=False, failed=1, errors=0, pass_rate=0.0
    )

    with patch("scaffold.agent.orchestrator.TestRunner") as runner_cls:
        runner_cls.return_value.run.return_value = failed_result
        results = orchestrator._run_native_graph_batch(
            graph, {"codebase_root": str(tmp_path), "exploration": {}}
        )

    assert [item["node_id"] for item in results] == ["tests"]
    assert results[0]["success"] is False
    assert graph.get_node("tests").status == ExecutionNodeStatus.FAILED
    assert graph.get_node("final").status == ExecutionNodeStatus.PENDING
