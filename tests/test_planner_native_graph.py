"""Provider-free tests for advanced Planner native graph output."""

from __future__ import annotations

import json
from types import SimpleNamespace

from scaffold.agent.planner import Planner


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
        "complexity": "medium",
    }
    node.update(overrides)
    return node


class FakeCompletions:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.text))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=10),
        )


class FakeChat:
    def __init__(self, text: str) -> None:
        self.completions = FakeCompletions(text)


class FakeClient:
    def __init__(self, text: str) -> None:
        self.chat = FakeChat(text)


def make_planner(response: dict) -> tuple[Planner, FakeClient]:
    client = FakeClient(json.dumps(response))
    planner = Planner.__new__(Planner)
    planner.client = client
    planner.model = "qwen3.7-plus"
    return planner, client


def test_advanced_planner_requests_structured_native_graph() -> None:
    response = {
        "execution_graph": {
            "graph_version": "1",
            "nodes": [
                _node("explore", "explore"),
                _node(
                    "edit",
                    "edit",
                    depends_on=["explore"],
                    output_files=["src/app.py"],
                    acceptance_check="python -m py_compile src/app.py",
                    complexity="low",
                ),
                _node("tests", "test", depends_on=["edit"], acceptance_check="pytest"),
            ],
        },
        "reasoning": "Explore before editing and test after the edit.",
    }
    planner, client = make_planner(response)

    result = planner.plan("Implement the app change", {"modules": "src"})

    request = client.chat.completions.calls[0]
    assert request["model"] == "qwen3.7-plus"
    assert request["response_format"] == {"type": "json_object"}
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}
    assert [message["role"] for message in request["messages"]] == ["system", "user"]
    assert result["execution_graph"]["graph_version"] == "1"
    assert [node["node_id"] for node in result["execution_graph"]["nodes"]] == [
        "explore", "edit", "tests"
    ]
    assert [task["task_id"] for task in result["plan"]] == ["edit"]
    assert result["total_tasks"] == 1


def test_legacy_planner_response_is_normalized_for_compatibility() -> None:
    planner, _ = make_planner(
        {
            "plan": [
                {
                    "task_id": 1,
                    "file": "src/app.py",
                    "action": "Implement app change",
                    "complexity": "low",
                }
            ],
            "reasoning": "One atomic edit.",
            "total_tasks": 1,
        }
    )

    result = planner.plan("Implement the app change", {"modules": "src"})

    assert result["execution_graph"]["nodes"][0]["node_id"] == "task:1"
    assert result["plan"][0]["task_id"] == 1
    assert result["plan"][0]["file"] == "src/app.py"
