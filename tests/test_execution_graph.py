"""Provider-free tests for the typed execution graph contract."""

from __future__ import annotations

from typing import Any, cast

import pytest

from scaffold.agent.execution_graph import (
    ExecutionGraph,
    ExecutionGraphError,
    ExecutionNode,
    ExecutionNodeStatus,
    ExecutionNodeType,
)


def node(node_id: str, node_type: ExecutionNodeType = ExecutionNodeType.BARRIER, **kwargs):
    return ExecutionNode(node_id, node_type, f"{node_id} operation", **kwargs)


def ids(wave):
    return [item.node_id for item in wave]


def test_all_node_types_and_statuses_serialize() -> None:
    for node_type in ExecutionNodeType:
        for status in ExecutionNodeStatus:
            current = ExecutionNode(
                f"{node_type.value}_{status.value}",
                node_type,
                "operation",
                status=status,
                acceptance_check="pytest tests/test_example.py",
                evidence_ref="snapshot:test",
                retry_limit=2,
                retry_count=1,
                metadata={"source": "planner"},
            )
            restored = ExecutionNode.from_dict(current.to_dict())
            assert restored == current


def test_graph_json_round_trip_preserves_nodes_and_state() -> None:
    graph = ExecutionGraph(
        [
            node("explore", ExecutionNodeType.EXPLORE, input_files=["src/app.py"]),
            node(
                "edit",
                ExecutionNodeType.EDIT,
                depends_on=["explore"],
                output_files=["src/app.py"],
                retry_limit=2,
            ),
        ]
    )
    graph.mark_status("explore", ExecutionNodeStatus.COMPLETED, evidence_ref="tree:1")

    restored = ExecutionGraph.from_dict(graph.to_dict())

    assert restored.to_dict() == graph.to_dict()
    restored_explore = restored.get_node("explore")
    assert restored_explore is not None
    assert restored_explore.evidence_ref == "tree:1"


def test_plan_tasks_compile_to_typed_nodes_with_stable_dependencies() -> None:
    graph = ExecutionGraph.from_plan_tasks(
        [
            {"task_id": 1, "file": "a.py", "action": "Implement A"},
            {
                "task_id": 2,
                "file": "b.py",
                "action": "Run focused tests",
                "node_type": "test",
                "depends_on": [1],
                "acceptance_check": "pytest tests/test_a.py",
            },
        ]
    )

    first = graph.get_node("task:1")
    second = graph.get_node("task:2")
    assert first is not None and second is not None
    assert first.node_type == ExecutionNodeType.EDIT
    assert second.node_type == ExecutionNodeType.TEST
    assert second.depends_on == ["task:1"]
    assert second.read_only is True
    assert second.metadata["task_id"] == 2
    assert second.acceptance_check == "pytest tests/test_a.py"


def test_plan_compiler_rejects_malformed_tasks() -> None:
    with pytest.raises(ExecutionGraphError, match="must be an object"):
        ExecutionGraph.from_plan_tasks(cast(Any, ["not a task"]))
    with pytest.raises(ExecutionGraphError, match="requires task_id"):
        ExecutionGraph.from_plan_tasks([{"file": "a.py", "action": "edit"}])
    with pytest.raises(ExecutionGraphError, match="requires action"):
        ExecutionGraph.from_plan_tasks([{"task_id": 1, "file": "a.py"}])
    with pytest.raises(ExecutionGraphError, match="unknown node type"):
        ExecutionGraph.from_plan_tasks(
            [{"task_id": 1, "action": "edit", "node_type": "unknown"}]
        )
    with pytest.raises(ExecutionGraphError, match="depends_on must be a list"):
        ExecutionGraph.from_plan_tasks(
            [{"task_id": 1, "action": "edit", "depends_on": "bad"}]
        )


def test_plan_compiler_rejects_duplicate_and_unknown_dependencies() -> None:
    with pytest.raises(ExecutionGraphError, match="duplicate planner task_id"):
        ExecutionGraph.from_plan_tasks(
            [
                {"task_id": 1, "action": "first"},
                {"task_id": 1, "action": "duplicate"},
            ]
        )
    with pytest.raises(ExecutionGraphError, match="unknown execution dependencies"):
        ExecutionGraph.from_plan_tasks(
            [{"task_id": 1, "action": "edit", "depends_on": [99]}]
        )


def test_empty_graph_is_valid_and_has_no_ready_waves() -> None:
    graph = ExecutionGraph()

    graph.validate()

    assert graph.ready_waves() == []


def test_duplicate_node_ids_are_rejected() -> None:
    graph = ExecutionGraph([node("same")])

    with pytest.raises(ExecutionGraphError, match="duplicate execution node"):
        graph.add_node(node("same"))


def test_unknown_dependency_is_rejected() -> None:
    with pytest.raises(ExecutionGraphError, match="unknown execution dependencies"):
        ExecutionGraph([node("child", depends_on=["missing"])])


def test_invalid_node_type_and_status_are_rejected() -> None:
    with pytest.raises(ExecutionGraphError, match="unknown execution node type"):
        ExecutionNode("bad", "not-a-node-type", "operation")
    with pytest.raises(ExecutionGraphError, match="unknown execution node status"):
        ExecutionNode("bad", ExecutionNodeType.BARRIER, "operation", status="unknown")


def test_dependency_cycles_are_rejected() -> None:
    first = node("first", depends_on=[])
    second = node("second", depends_on=["first"])
    graph = ExecutionGraph([first, second])
    first.depends_on = ["second"]

    with pytest.raises(ExecutionGraphError, match="dependency cycle"):
        graph.validate()


def test_independent_nodes_share_a_deterministic_wave() -> None:
    graph = ExecutionGraph([node("b"), node("a"), node("join", depends_on=["a", "b"])])

    waves = graph.ready_waves()

    assert [ids(wave) for wave in waves] == [["a", "b"], ["join"]]
    assert all(item.status == ExecutionNodeStatus.PENDING for wave in waves for item in wave)


def test_same_file_writes_are_serialized() -> None:
    graph = ExecutionGraph(
        [
            node("second", ExecutionNodeType.EDIT, output_files=["src/shared.py"]),
            node("first", ExecutionNodeType.EDIT, output_files=["src/shared.py"]),
        ]
    )

    waves = graph.ready_waves()

    assert [ids(wave) for wave in waves] == [["first"], ["second"]]


def test_read_only_nodes_do_not_conflict_with_writes() -> None:
    graph = ExecutionGraph(
        [
            node("inspect", ExecutionNodeType.EDIT, input_files=["src/shared.py"], read_only=True),
            node("write", ExecutionNodeType.EDIT, output_files=["src/shared.py"]),
        ]
    )

    assert [ids(wave) for wave in graph.ready_waves()] == [["inspect", "write"]]


def test_test_barrier_waits_for_all_prerequisites() -> None:
    graph = ExecutionGraph(
        [
            node("edit_a", ExecutionNodeType.EDIT, output_files=["a.py"]),
            node("edit_b", ExecutionNodeType.EDIT, output_files=["b.py"]),
            node(
                "tests",
                ExecutionNodeType.TEST,
                depends_on=["edit_a", "edit_b"],
                acceptance_check="pytest",
            ),
            node("review", ExecutionNodeType.REVIEW, depends_on=["tests"]),
        ]
    )

    assert [ids(wave) for wave in graph.ready_waves()] == [
        ["edit_a", "edit_b"],
        ["tests"],
        ["review"],
    ]


def test_failed_prerequisite_blocks_normal_downstream_node() -> None:
    graph = ExecutionGraph(
        [
            node("tests", ExecutionNodeType.TEST),
            node("final", ExecutionNodeType.FINAL_VERIFY, depends_on=["tests"]),
        ]
    )
    graph.mark_status("tests", ExecutionNodeStatus.FAILED)

    assert graph.ready_waves() == []


def test_recovery_unlocks_after_failure_and_retry_follows_recovery() -> None:
    graph = ExecutionGraph([node("tests", ExecutionNodeType.TEST)])
    graph.mark_status("tests", ExecutionNodeStatus.FAILED)
    recovery = node("repair", ExecutionNodeType.RECOVERY, retry_limit=1)
    graph.add_recovery_node("tests", recovery)
    graph.add_node(node("retry", ExecutionNodeType.TEST, depends_on=["repair"], retry_limit=1))

    assert [ids(wave) for wave in graph.ready_waves()] == [["repair"], ["retry"]]


def test_recovery_insertion_requires_failed_target_and_recovery_type() -> None:
    graph = ExecutionGraph([node("tests")])

    with pytest.raises(ExecutionGraphError, match="requires failed node status"):
        graph.add_recovery_node("tests", node("repair", ExecutionNodeType.RECOVERY))

    graph.mark_status("tests", ExecutionNodeStatus.FAILED)
    with pytest.raises(ExecutionGraphError, match="node_type='recovery'"):
        graph.add_recovery_node("tests", node("repair", ExecutionNodeType.EDIT))


def test_retry_bounds_are_rejected() -> None:
    with pytest.raises(ExecutionGraphError, match="non-negative"):
        node("negative", retry_limit=-1)
    with pytest.raises(ExecutionGraphError, match="cannot exceed"):
        node("over", retry_limit=1, retry_count=2)


def test_malformed_graph_json_is_rejected() -> None:
    with pytest.raises(ExecutionGraphError, match="nodes list"):
        ExecutionGraph.from_dict({})
    with pytest.raises(ExecutionGraphError, match="execution node data"):
        ExecutionGraph.from_dict({"nodes": ["not an object"]})
