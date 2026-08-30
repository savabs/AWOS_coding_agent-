"""Provider-free routing tests for the advanced OpenCode Qwen Planner."""

from __future__ import annotations

import json
from math import isclose
from types import SimpleNamespace

from scaffold.agent.fixed_provider import FIXED_MODEL, PLANNER_MODEL
from scaffold.agent.planner import Planner


def _response() -> SimpleNamespace:
    graph = {
        "execution_graph": {
            "graph_version": "1",
            "nodes": [
                {
                    "node_id": "edit_app",
                    "type": "edit",
                    "description": "Implement the app change",
                    "depends_on": [],
                    "input_files": [],
                    "output_files": ["src/app.py"],
                    "acceptance_check": "pytest tests/test_app.py",
                    "evidence_ref": None,
                    "parallel_safe": True,
                    "retry_limit": 1,
                    "complexity": "low",
                }
            ],
        },
        "reasoning": "One atomic edit is sufficient.",
    }
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(graph)))],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50),
    )


def test_planner_role_uses_qwen_and_worker_role_stays_deepseek() -> None:
    assert PLANNER_MODEL == "qwen3.7-plus"
    assert FIXED_MODEL == "deepseek-v4-pro"


def test_planner_uses_opencode_key_without_anthropic(monkeypatch) -> None:
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_kwargs: _response()))
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "test-key")
    monkeypatch.setattr("scaffold.agent.planner.create_fixed_client", lambda key: client)

    planner = Planner()

    assert planner.model == PLANNER_MODEL
    assert planner.api_key == "test-key"


def test_planner_records_qwen_opencode_cost(monkeypatch) -> None:
    calls: list[dict] = []

    class Completions:
        def create(self, **_kwargs):
            return _response()

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    planner = Planner.__new__(Planner)
    planner.client = client
    planner.model = PLANNER_MODEL
    tracker = SimpleNamespace(
        record=lambda **kwargs: calls.append(kwargs),
    )

    result = planner.plan("Implement the app change", {"modules": "src"}, tracker=tracker)

    assert result["execution_graph"]["graph_version"] == "1"
    assert len(calls) == 1
    assert calls[0]["request_type"] == "planning"
    assert calls[0]["model"] == "OpenCode Go qwen3.7-plus"
    assert calls[0]["input_tokens"] == 100
    assert calls[0]["output_tokens"] == 50
    assert isclose(calls[0]["cost"], 0.00012, rel_tol=1e-12)
