"""Regression tests for the current DeepSeek Pro via OpenCode Go path."""

from types import SimpleNamespace

import scaffold.agent.worker as worker_module
from scaffold.agent.worker import Worker


class _FakeResponse:
    def __init__(self, content: str):
        self.choices = [SimpleNamespace(message=SimpleNamespace(content=content))]
        self.usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)


class _FakeCompletions:
    def __init__(self, content: str):
        self.content = content
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResponse(self.content)


class _FakeClient:
    def __init__(self, content: str):
        self.completions = _FakeCompletions(content)
        self.chat = SimpleNamespace(completions=self.completions)


class _FreeLedger:
    def check_budget(self, *_args):
        return True, "ok"


def _model_spec():
    return SimpleNamespace(
        provider="opencode",
        model_id="deepseek-v4-pro",
        name="DeepSeek V4 Pro",
        cost_per_req=0.002,
        input_price=0.44,
        output_price=0.88,
    )


def _worker(client):
    worker = Worker.__new__(Worker)
    worker.opencode_client = client
    worker.client = None
    worker.openai_client = None
    worker.anthropic_client = None
    worker.model = "deepseek-v4-pro"
    worker.fallback_model = "claude-haiku-4-5"
    worker._dead_providers = set()
    return worker


def test_execute_task_uses_opencode_for_deepseek_pro(monkeypatch):
    client = _FakeClient("CONTENT:\n```python\n# generated\n```\n")
    worker = _worker(client)
    monkeypatch.setattr(worker_module, "get_ledger", lambda: _FreeLedger())
    monkeypatch.setattr(Worker, "_build_prompt", lambda *args, **kwargs: "prompt")

    result = worker.execute_task(
        task={"task_id": 1, "file": "new.py", "action": "create file", "complexity": "low", "_is_new_file": True},
        file_content="",
        codebase_context={},
        model_spec=_model_spec(),
    )

    assert result["success"] is True
    assert result["model_used"] == "DeepSeek V4 Pro"
    assert len(client.chat.completions.calls) == 1
    assert client.chat.completions.calls[0]["model"] == "deepseek-v4-pro"


def test_architect_step_uses_opencode_model(monkeypatch):
    client = _FakeClient("Use the existing function and preserve its contract.")
    worker = _worker(client)

    result = worker._architect_step(
        action="create a safe helper",
        context_snippet="def existing(): pass",
        file_path="new.py",
        model_spec=_model_spec(),
    )

    assert result == "Use the existing function and preserve its contract."
    assert client.chat.completions.calls[0]["model"] == "deepseek-v4-pro"
