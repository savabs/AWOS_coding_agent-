"""Provider-free tests for the advanced Planner graph contract."""

from __future__ import annotations

import pytest

from scaffold.agent.planner_graph_contract import (
    PlannerGraphContractError,
    graph_from_legacy_plan,
    planner_graph_json_schema,
    project_legacy_plan,
    validate_planner_graph,
)


def graph_node(node_id: str, node_type: str, **overrides) -> dict:
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
        "complexity": "medium",
    }
    node.update(overrides)
    return node


def test_schema_declares_all_typed_node_fields() -> None:
    schema = planner_graph_json_schema()
    node_schema = schema["properties"]["nodes"]["items"]

    assert schema["properties"]["graph_version"]["const"] == "1"
    assert set(node_schema["properties"]) == {
        "node_id",
        "type",
        "description",
        "depends_on",
        "input_files",
        "output_files",
        "acceptance_check",
        "evidence_ref",
        "parallel_safe",
        "retry_limit",
        "complexity",
        "function_signature",
        "interface_contract",
        "constraints",
        "must_not",
        "example_call",
        "planner_metadata",
    }


def test_valid_graph_accepts_all_node_types_and_forward_dependencies() -> None:
    graph = validate_planner_graph(
        {
            "graph_version": "1",
            "nodes": [
                graph_node("explore", "explore"),
                graph_node("edit", "edit", depends_on=["explore"], output_files=["src/a.py"]),
                graph_node(
                    "tests",
                    "test",
                    depends_on=["edit"],
                    acceptance_check="pytest tests/test_a.py",
                ),
                graph_node("barrier", "barrier", depends_on=["tests"], parallel_safe=False),
                graph_node("review", "review", depends_on=["barrier"]),
                graph_node("repair", "recovery", depends_on=["tests"], output_files=["src/a.py"]),
                graph_node("final", "final_verify", depends_on=["review"], parallel_safe=False),
            ],
        }
    )

    assert graph["graph_version"] == "1"
    assert [node["node_id"] for node in graph["nodes"]] == [
        "explore", "edit", "tests", "barrier", "review", "repair", "final"
    ]


def test_projection_contains_only_file_changing_nodes() -> None:
    graph = validate_planner_graph(
        {
            "graph_version": "1",
            "nodes": [
                graph_node("explore", "explore"),
                graph_node(
                    "edit_auth",
                    "edit",
                    depends_on=["explore"],
                    output_files=["src/auth.py"],
                    acceptance_check="python -m py_compile src/auth.py",
                    complexity="high",
                ),
                graph_node("tests", "test", depends_on=["edit_auth"], acceptance_check="pytest"),
            ],
        }
    )

    plan = project_legacy_plan(graph)

    assert len(plan) == 1
    assert plan[0]["task_id"] == "edit_auth"
    assert plan[0]["file"] == "src/auth.py"
    assert plan[0]["depends_on"] == ["explore"]
    assert plan[0]["complexity"] == "high"


def test_legacy_plan_is_wrapped_into_native_graph() -> None:
    graph = graph_from_legacy_plan(
        [
            {
                "task_id": 1,
                "file": "src/a.py",
                "action": "Implement A",
                "complexity": "low",
            }
        ]
    )

    assert graph["graph_version"] == "1"
    assert graph["nodes"][0]["node_id"] == "task:1"
    assert graph["nodes"][0]["type"] == "edit"
    assert graph["nodes"][0]["output_files"] == ["src/a.py"]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("node_id", "", "node_id"),
        ("description", "", "description"),
        ("retry_limit", 4, "retry_limit"),
        ("parallel_safe", "yes", "parallel_safe"),
        ("output_files", [], "requires output_files"),
    ],
)
def test_invalid_node_fields_fail_closed(field: str, value, message: str) -> None:
    node = graph_node("edit", "edit", output_files=["src/a.py"])
    node[field] = value

    with pytest.raises(PlannerGraphContractError, match=message):
        validate_planner_graph({"graph_version": "1", "nodes": [node]})


def test_unknown_fields_version_dependencies_and_cycles_fail_closed() -> None:
    with pytest.raises(PlannerGraphContractError, match="schema mismatch"):
        validate_planner_graph({"graph_version": "1", "nodes": [graph_node("a", "explore", extra="x")]})
    with pytest.raises(PlannerGraphContractError, match="unsupported"):
        validate_planner_graph({"graph_version": "2", "nodes": [graph_node("a", "explore")]})
    with pytest.raises(PlannerGraphContractError, match="unknown planner dependencies"):
        validate_planner_graph({"graph_version": "1", "nodes": [graph_node("a", "explore", depends_on=["missing"])]})

    first = graph_node("a", "explore", depends_on=["b"])
    second = graph_node("b", "explore", depends_on=["a"])
    with pytest.raises(PlannerGraphContractError, match="dependency cycle"):
        validate_planner_graph({"graph_version": "1", "nodes": [first, second]})


def test_type_specific_safety_rules_fail_closed() -> None:
    with pytest.raises(PlannerGraphContractError, match="test nodes require"):
        validate_planner_graph({"graph_version": "1", "nodes": [graph_node("tests", "test")]})
    with pytest.raises(PlannerGraphContractError, match="cannot declare output_files"):
        validate_planner_graph({"graph_version": "1", "nodes": [graph_node("review", "review", output_files=["review.md"])]})
    with pytest.raises(PlannerGraphContractError, match="cannot be marked parallel_safe"):
        validate_planner_graph({"graph_version": "1", "nodes": [graph_node("barrier", "barrier")]})
    with pytest.raises(PlannerGraphContractError, match="unsafe workspace path"):
        validate_planner_graph({"graph_version": "1", "nodes": [graph_node("edit", "edit", output_files=["../escape.py"])]})
