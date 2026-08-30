"""Provider-free tests for Qwen Planner graph-hint compatibility."""

from __future__ import annotations

import pytest

from scaffold.agent.orchestrator import Orchestrator
from scaffold.agent.planner_graph_contract import PlannerGraphContractError, validate_planner_graph


def _node(**overrides) -> dict:
    node = {
        "node_id": "edit_task",
        "type": "edit",
        "description": "Implement the task",
        "depends_on": [],
        "input_files": ["src/app.py"],
        "output_files": ["src/app.py"],
        "acceptance_check": "python -m py_compile src/app.py",
        "evidence_ref": None,
        "parallel_safe": True,
        "retry_limit": 1,
        "complexity": "medium",
    }
    node.update(overrides)
    return node


def test_qwen_prompt_contract_hints_are_normalized_and_preserved() -> None:
    graph = validate_planner_graph({
        "graph_version": "1",
        "nodes": [_node(
            function_signature="def run(value: str) -> int:",
            interface_contract="Call run with a validated string.",
            constraints=["Reject empty input"],
            must_not=["Do not mutate global state"],
            example_call="run('value')",
        )],
    })

    normalized = graph["nodes"][0]
    assert normalized["planner_metadata"]["function_signature"] == "def run(value: str) -> int:"
    assert normalized["planner_metadata"]["constraints"] == ["Reject empty input"]
    assert normalized["planner_metadata"]["must_not"] == ["Do not mutate global state"]

    execution_graph = Orchestrator._restore_native_execution_graph(
        Orchestrator.__new__(Orchestrator), graph
    )
    task = Orchestrator._native_node_task(execution_graph.nodes["edit_task"])
    assert task["function_signature"] == "def run(value: str) -> int:"
    assert task["interface_contract"] == "Call run with a validated string."
    assert task["example_call"] == "run('value')"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("function_signature", 42),
        ("interface_contract", ["wrong type"]),
        ("example_call", ""),
        ("constraints", ["ok", 3]),
        ("must_not", ["x" * 501]),
    ],
)
def test_malformed_planner_hints_fail_closed(field: str, value) -> None:
    with pytest.raises(PlannerGraphContractError, match=field):
        validate_planner_graph({"graph_version": "1", "nodes": [_node(**{field: value})]})


def test_unseen_metadata_is_preserved_but_not_promoted_to_execution() -> None:
    graph = validate_planner_graph({
        "graph_version": "1",
        "nodes": [_node(planner_metadata={
            "performance_requirements": ["handle 10000 records"],
            "migration_notes": {"compatibility": "keep API stable"},
        })],
    })

    metadata = graph["nodes"][0]["planner_metadata"]
    assert metadata["performance_requirements"] == ["handle 10000 records"]
    assert metadata["migration_notes"]["compatibility"] == "keep API stable"
    execution_graph = Orchestrator._restore_native_execution_graph(
        Orchestrator.__new__(Orchestrator), graph
    )
    task = Orchestrator._native_node_task(execution_graph.nodes["edit_task"])
    assert "performance_requirements" not in task


def test_arbitrary_model_fields_remain_rejected() -> None:
    with pytest.raises(PlannerGraphContractError, match="schema mismatch"):
        validate_planner_graph({
            "graph_version": "1",
            "nodes": [_node(unreviewed_model_instruction="execute anything")],
        })


def test_duplicate_legacy_and_metadata_hint_fails_closed() -> None:
    with pytest.raises(PlannerGraphContractError, match="duplicates"):
        validate_planner_graph({
            "graph_version": "1",
            "nodes": [_node(
                function_signature="def run() -> None:",
                planner_metadata={"function_signature": "def other() -> None:"},
            )],
        })


def test_metadata_bounds_fail_closed() -> None:
    with pytest.raises(PlannerGraphContractError, match="depth"):
        validate_planner_graph({
            "graph_version": "1",
            "nodes": [_node(planner_metadata={"a": {"b": {"c": {"d": {"e": "too deep"}}}}})],
        })
