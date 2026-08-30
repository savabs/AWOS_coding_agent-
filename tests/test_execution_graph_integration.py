"""Provider-free tests for the typed graph execution adapter."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from scaffold.agent.dag_executor import DAGExecutor
from scaffold.agent.orchestrator import Orchestrator
from scaffold.agent.runtime_session import RuntimeSessionStore
from scaffold.agent.execution_graph import (
    ExecutionGraph,
    ExecutionGraphError,
    ExecutionNode,
    ExecutionNodeStatus,
    ExecutionNodeType,
)


def get_node(graph: ExecutionGraph, node_id: str) -> ExecutionNode:
    result = graph.get_node(node_id)
    assert result is not None
    return result


def make_node(
    node_id: str,
    node_type: ExecutionNodeType = ExecutionNodeType.BARRIER,
    depends_on: list[str] | None = None,
) -> ExecutionNode:
    return ExecutionNode(
        node_id=node_id,
        node_type=node_type,
        description=f"Run {node_id}",
        depends_on=depends_on or [],
    )


def test_execute_graph_runs_waves_and_updates_evidence() -> None:
    graph = ExecutionGraph(
        [
            make_node("edit_a", ExecutionNodeType.EDIT),
            make_node("edit_b", ExecutionNodeType.EDIT),
            make_node("tests", ExecutionNodeType.TEST, ["edit_a", "edit_b"]),
        ]
    )
    seen: list[str] = []

    def run(node: ExecutionNode) -> dict:
        seen.append(node.node_id)
        return {"success": True, "evidence_ref": f"proof:{node.node_id}"}

    results = DAGExecutor([]).execute_graph(graph, run)

    assert seen[:2] == ["edit_a", "edit_b"]
    assert seen[2:] == ["tests"]
    assert [result["node_id"] for result in results] == ["edit_a", "edit_b", "tests"]
    assert all(node.status == ExecutionNodeStatus.COMPLETED for node in graph.nodes.values())
    assert get_node(graph, "tests").evidence_ref == "proof:tests"


def test_execute_graph_does_not_release_downstream_after_failure() -> None:
    graph = ExecutionGraph(
        [
            make_node("tests", ExecutionNodeType.TEST),
            make_node("final", ExecutionNodeType.FINAL_VERIFY, ["tests"]),
        ]
    )
    called: list[str] = []

    def run(node: ExecutionNode) -> dict:
        called.append(node.node_id)
        return {"success": False, "error": "test failure"}

    results = DAGExecutor([]).execute_graph(graph, run)

    assert [result["node_id"] for result in results] == ["tests"]
    assert called == ["tests"]
    assert get_node(graph, "tests").status == ExecutionNodeStatus.FAILED
    assert get_node(graph, "final").status == ExecutionNodeStatus.PENDING


def test_execute_graph_allows_explicit_recovery_after_failure() -> None:
    graph = ExecutionGraph([make_node("tests", ExecutionNodeType.TEST)])
    graph.mark_status("tests", ExecutionNodeStatus.FAILED)
    graph.add_recovery_node("tests", make_node("repair", ExecutionNodeType.RECOVERY))
    called: list[str] = []

    def run(node: ExecutionNode) -> dict:
        called.append(node.node_id)
        return {"success": True}

    results = DAGExecutor([]).execute_graph(graph, run)

    assert [result["node_id"] for result in results] == ["repair"]
    assert called == ["repair"]
    assert get_node(graph, "tests").status == ExecutionNodeStatus.FAILED
    assert get_node(graph, "repair").status == ExecutionNodeStatus.COMPLETED


def test_execute_graph_fails_closed_on_cycle_instead_of_forcing_a_node() -> None:
    first = make_node("first")
    second = make_node("second", depends_on=["first"])
    graph = ExecutionGraph([first, second])
    first.depends_on = ["second"]

    with pytest.raises(ExecutionGraphError, match="dependency cycle"):
        DAGExecutor([]).execute_graph(graph, lambda _: {"success": True})

    assert first.status == ExecutionNodeStatus.PENDING
    assert second.status == ExecutionNodeStatus.PENDING


def test_execute_graph_checkpoints_each_completed_wave() -> None:
    graph = ExecutionGraph(
        [
            make_node("edit", ExecutionNodeType.EDIT),
            make_node("tests", ExecutionNodeType.TEST, ["edit"]),
        ]
    )
    checkpoints: list[tuple[str, str]] = []

    def checkpoint(current_graph: ExecutionGraph, wave_results: list[dict]) -> None:
        current = get_node(current_graph, wave_results[0]["node_id"])
        checkpoints.append(
            (wave_results[0]["node_id"], ExecutionNodeStatus(current.status).value)
        )

    DAGExecutor([]).execute_graph(
        graph,
        lambda node: {"success": True, "evidence_ref": f"proof:{node.node_id}"},
        on_wave_complete=checkpoint,
    )

    assert checkpoints == [("edit", "completed"), ("tests", "completed")]


def test_failure_recovery_and_resume_persist_through_runtime_session(tmp_path) -> None:
    store = RuntimeSessionStore(sessions_dir=str(tmp_path / "sessions"))
    session = store.create("recover graph", str(tmp_path))
    graph = ExecutionGraph([make_node("tests", ExecutionNodeType.TEST)])

    def save_graph(current_graph: ExecutionGraph, _wave_results: list[dict]) -> None:
        session.execution_graph = current_graph.to_dict()
        store.save(session)

    failed = DAGExecutor([]).execute_graph(
        graph,
        lambda _: {"success": False, "evidence_ref": "proof:failed"},
        on_wave_complete=save_graph,
    )
    assert failed[0]["success"] is False

    persisted = store.load(session.session_id)
    assert persisted.execution_graph is not None
    resumed_graph = ExecutionGraph.from_dict(persisted.execution_graph)
    resumed_graph.add_recovery_node("tests", make_node("repair", ExecutionNodeType.RECOVERY))
    session.execution_graph = resumed_graph.to_dict()
    store.save(session)

    resumed = store.load(session.session_id)
    assert resumed.execution_graph is not None
    resumed_graph = ExecutionGraph.from_dict(resumed.execution_graph)
    recovery_results = DAGExecutor([]).execute_graph(
        resumed_graph,
        lambda _: {"success": True, "evidence_ref": "proof:repair"},
        on_wave_complete=save_graph,
    )

    assert [result["node_id"] for result in recovery_results] == ["repair"]
    final_session = store.load(session.session_id)
    assert final_session.execution_graph is not None
    final = ExecutionGraph.from_dict(final_session.execution_graph)
    assert get_node(final, "tests").status == ExecutionNodeStatus.FAILED
    assert get_node(final, "repair").status == ExecutionNodeStatus.COMPLETED
    assert get_node(final, "repair").evidence_ref == "proof:repair"


def test_orchestrator_parallel_batch_routes_through_typed_graph() -> None:
    orchestrator = Orchestrator.__new__(Orchestrator)
    tasks = [
        {"task_id": 1, "file": "a.py", "action": "Edit A"},
        {"task_id": 2, "file": "b.py", "action": "Edit B"},
        {"task_id": 3, "file": "c.py", "action": "Join", "depends_on": [1, 2]},
    ]
    seen: list[int] = []

    def execute_task(task: dict, _: dict) -> dict:
        seen.append(task["task_id"])
        return {"task_id": task["task_id"], "task": task, "success": True}

    with patch.object(orchestrator, "_execute_single_task", side_effect=execute_task):
        results = orchestrator._run_task_batch(tasks, {}, use_parallel=True)

    assert seen[:2] == [1, 2]
    assert seen[2:] == [3]
    assert [result["task_id"] for result in results] == [1, 2, 3]


def test_execute_graph_converts_callback_exceptions_to_failed_nodes() -> None:
    graph = ExecutionGraph([make_node("broken", ExecutionNodeType.EDIT)])

    def run(_: ExecutionNode) -> dict:
        raise RuntimeError("worker unavailable")

    results = DAGExecutor([]).execute_graph(graph, run)

    assert results[0]["success"] is False
    assert "worker unavailable" in results[0]["error"]
    assert get_node(graph, "broken").status == ExecutionNodeStatus.FAILED
