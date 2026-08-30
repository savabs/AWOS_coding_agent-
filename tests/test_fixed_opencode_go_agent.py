from types import SimpleNamespace

import pytest

from scaffold.agent.evaluation_v1 import AcceptanceCheck, EvaluationFixture, make_orchestrator_agent
from scaffold.agent.fixed_provider import FIXED_MODEL, FIXED_PROVIDER
from scaffold.agent.goal_clarifier import AmbiguityDetector
from scaffold.agent.cheap_planner import CheapPlanner, _response_shape
from scaffold.agent.coding_tools import RunTestsTool
from scaffold.agent.orchestrator import Orchestrator
from scaffold.agent.test_runner import TestRunner
from scaffold.agent.escalation_engine import EscalationEngine
from scaffold.agent.react_worker import ReActWorker
from scaffold.agent.worker import Worker


class FakeCompletions:
    def __init__(self, content="IS_CLEAR: yes\nMISSING_ELEMENTS:\n", *, reasoning_content=None, tool_calls=None):
        self.content = content
        self.reasoning_content = reasoning_content
        self.tool_calls = tool_calls
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(
                content=self.content,
                reasoning_content=self.reasoning_content,
                tool_calls=self.tool_calls,
            ))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )


class FakeClient:
    def __init__(self, content="IS_CLEAR: yes\nMISSING_ELEMENTS:\n", *, reasoning_content=None, tool_calls=None):
        self.chat = SimpleNamespace(completions=FakeCompletions(
            content,
            reasoning_content=reasoning_content,
            tool_calls=tool_calls,
        ))
        self.completions = self.chat.completions


def test_fixed_provider_contract():
    assert FIXED_PROVIDER == "opencode"
    assert FIXED_MODEL == "deepseek-v4-pro"


def test_benchmark_run_tests_uses_fixture_interpreter_and_suppresses_bytecode(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_SAFE_TO_RUN_TESTS", "1")
    monkeypatch.setenv("AWOS_TEST_PYTHON", "/tmp/fixture-python")
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout="1 passed in 0.01s\n", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)

    result = RunTestsTool(tmp_path).execute({"paths": "test_std_L060_hidden.py"})

    assert result.success is True
    assert calls[0][0][:3] == ["/tmp/fixture-python", "-m", "pytest"]
    assert calls[0][1]["env"]["PYTHONDONTWRITEBYTECODE"] == "1"


def test_benchmark_test_execution_leaves_no_workspace_state(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_SAFE_TO_RUN_TESTS", "1")
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_property.py").write_text(
        "from hypothesis import given, strategies as st\n\n"
        "@given(st.integers())\n"
        "def test_identity(value):\n"
        "    assert value == value\n"
    )

    result = TestRunner(project_root=str(tmp_path)).run()

    assert result.pass_rate == 1.0
    assert not (tmp_path / ".pytest_cache").exists()
    assert not (tmp_path / ".hypothesis").exists()
    assert not list(tmp_path.rglob("__pycache__"))


def test_benchmark_adapter_bounds_one_allowed_path(tmp_path):
    captured = {}

    class FakeOrchestrator:
        def execute_feature(self, **kwargs):
            captured.update(kwargs)
            return {
                "success": True,
                "total_cost": 0.0,
                "tasks_completed": 1,
                "tasks_failed": 0,
                "execution_log": [],
            }

    fixture = EvaluationFixture(
        task_id="sqlfluff__sqlfluff-2419",
        family="swebench_lite",
        repository_path="repository",
        request="Change the L060 violation message.",
        acceptance=[AcceptanceCheck(command=["python", "-m", "pytest", "-q"])],
        allowed_paths=["src/sqlfluff/rules/L060.py"],
    )
    agent = make_orchestrator_agent(FakeOrchestrator, benchmark_mode=True)
    agent(fixture, tmp_path, lambda *_: None)

    assert len(captured["pre_planned_tasks"]) == 1
    assert captured["pre_planned_tasks"][0]["file"] == "src/sqlfluff/rules/L060.py"


def test_benchmark_escalation_is_fixed_to_opencode_pro(monkeypatch):
    monkeypatch.setenv("AWOS_BENCHMARK_MODE", "1")

    decision = EscalationEngine().decide(
        task={"action": "modify the target implementation", "complexity": "low"},
        budget_remaining=0.50,
    )

    assert decision.spec.provider == FIXED_PROVIDER
    assert decision.spec.model_id == FIXED_MODEL


def test_benchmark_orchestrator_creates_trial_local_budget_tracker(monkeypatch):
    created = []

    class FakeTracker:
        def __init__(self, monthly_budget):
            self.monthly_budget = monthly_budget
            created.append(self)

    monkeypatch.setenv("AWOS_BENCHMARK_MODE", "1")
    monkeypatch.setenv("AWOS_BUDGET_USD", "0.50")
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "test-key")
    monkeypatch.setattr("scaffold.agent.orchestrator.TokenTracker", FakeTracker)

    orchestrator = Orchestrator()

    assert len(created) == 1
    assert orchestrator.tracker is created[0]
    assert created[0].monthly_budget == pytest.approx(0.50)


def test_goal_clarifier_uses_fixed_opencode_model(monkeypatch):
    client = FakeClient()
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "test-key")
    monkeypatch.setattr("scaffold.agent.goal_clarifier.create_fixed_client", lambda: client)

    ambiguous, missing = AmbiguityDetector().detect("Modify planner.py and run tests")

    assert ambiguous is False
    assert missing == []
    assert client.completions.calls[0]["model"] == FIXED_MODEL


def test_goal_clarifier_without_opencode_uses_no_alternate_provider(monkeypatch):
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "direct-key")

    ambiguous, missing = AmbiguityDetector().detect("Fix the bug")

    assert ambiguous is True
    assert "output" in missing


def test_cheap_planner_ignores_dynamic_model(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr("scaffold.agent.cheap_planner.create_fixed_client", lambda key: client)

    planner = CheapPlanner(api_key="test-key", model="qwen3.7-plus")

    assert planner.model_name == FIXED_MODEL


def test_response_shape_classifies_provider_message_variants():
    assert _response_shape(SimpleNamespace(content='{"plan": []}')) == "content"
    assert _response_shape(SimpleNamespace(content=None, reasoning_content="thinking")) == "reasoning_only"
    assert _response_shape(SimpleNamespace(content="", tool_calls=[{"id": "call_1"}])) == "tool_call_only"
    assert _response_shape(SimpleNamespace(content="")) == "empty"
    assert _response_shape(None) == "unavailable"


def test_cheap_planner_requests_nonthinking_json_output(monkeypatch):
    client = FakeClient(content='{"plan": [], "reasoning": "ok", "total_tasks": 0}')
    monkeypatch.setattr("scaffold.agent.cheap_planner.create_fixed_client", lambda key: client)

    CheapPlanner(api_key="test-key").plan("Fix the bug", {})

    request = client.completions.calls[0]
    assert request["response_format"] == {"type": "json_object"}
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "temperature" not in request


def test_cheap_planner_reports_fixed_provider_on_malformed_json(monkeypatch):
    client = FakeClient(content='{"plan": [')
    monkeypatch.setattr("scaffold.agent.cheap_planner.create_fixed_client", lambda key: client)

    planner = CheapPlanner(api_key="test-key")

    with pytest.raises(ValueError, match=r"OpenCode Go.*deepseek-v4-pro.*response_shape=content"):
        planner.plan("Fix the bug", {})


def test_cheap_planner_distinguishes_reasoning_only_response(monkeypatch):
    client = FakeClient(content=None, reasoning_content="thinking")
    monkeypatch.setattr("scaffold.agent.cheap_planner.create_fixed_client", lambda key: client)

    with pytest.raises(ValueError, match=r"response_shape=reasoning_only"):
        CheapPlanner(api_key="test-key").plan("Fix the bug", {})


def test_cheap_planner_distinguishes_tool_call_only_response(monkeypatch):
    client = FakeClient(content="", tool_calls=[{"id": "call_1"}])
    monkeypatch.setattr("scaffold.agent.cheap_planner.create_fixed_client", lambda key: client)

    with pytest.raises(ValueError, match=r"response_shape=tool_call_only"):
        CheapPlanner(api_key="test-key").plan("Fix the bug", {})


def test_worker_requires_opencode_and_disables_alternates(monkeypatch):
    client = FakeClient()
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "direct-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setattr("scaffold.agent.worker.create_fixed_client", lambda key: client)

    worker = Worker()

    assert worker.model == FIXED_MODEL
    assert worker.opencode_client is client
    assert worker.client is None
    assert worker.anthropic_client is None
    assert worker.openai_client is None


def test_orchestrator_auxiliary_call_uses_fixed_model():
    client = FakeClient(content="critique")
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.worker = SimpleNamespace(opencode_client=client, client=FakeClient("wrong"))

    assert orchestrator._cheap_call("review") == "critique"
    assert client.completions.calls[0]["model"] == FIXED_MODEL


def test_react_default_model_is_fixed_opencode():
    worker = ReActWorker.__new__(ReActWorker)
    worker.opencode_client = FakeClient()
    worker.client = None
    worker.openrouter_client = None
    worker.gemini_client = None
    worker.anthropic_client = None
    worker._dead_providers = set()
    worker.model = FIXED_MODEL

    spec = worker._default_model_spec()

    assert spec.provider == FIXED_PROVIDER
    assert spec.model_id == FIXED_MODEL


def _react_worker_for_call(client):
    worker = ReActWorker.__new__(ReActWorker)
    worker.opencode_client = client
    worker.client = None
    worker.openrouter_client = None
    worker.gemini_client = None
    worker.anthropic_client = None
    worker._dead_providers = set()
    worker.model = FIXED_MODEL
    return worker


def test_react_opencode_request_uses_json_nonthinking_contract():
    client = FakeClient(content='{"thought":"done","action":"finish","action_input":{}}')
    worker = _react_worker_for_call(client)

    worker._call_model([{"role": "user", "content": "task"}])

    request = client.completions.calls[0]
    assert request["model"] == FIXED_MODEL
    assert request["response_format"] == {"type": "json_object"}
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "temperature" not in request


def test_cheap_planner_budget_guard_blocks_request(monkeypatch):
    client = FakeClient(content='{"plan": []}')
    monkeypatch.setattr("scaffold.agent.cheap_planner.create_fixed_client", lambda key: client)
    tracker = SimpleNamespace(check_guard=lambda estimated_cost: (False, "cap exhausted"))

    with pytest.raises(RuntimeError, match="Trial budget guard stopped planner request"):
        CheapPlanner(api_key="test-key").plan("Fix the bug", {}, tracker=tracker)
    assert client.completions.calls == []


def test_react_opencode_empty_content_is_structured_error():
    worker = _react_worker_for_call(FakeClient(content=None))

    with pytest.raises(RuntimeError, match="OpenCode Go returned empty response content"):
        worker._call_model([{"role": "user", "content": "task"}])
