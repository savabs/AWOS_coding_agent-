"""
tests/test_scaffold.py — Minimal acceptance tests for the agent scaffold.

Tests:
  Happy path: orchestrator runs with no tools and produces output
  Failure: tool registry returns fail result for unknown tool
  Failure: settings validation catches missing API key
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


from agent.config import Settings
from agent.core import Orchestrator
from agent.memory import MemoryStore
from agent.tools import Tool, ToolRegistry, ToolResult

# ── Test fixtures ────────────────────────────────────────────────────────


class EchoTool(Tool):
    """Simple tool that echoes its input."""

    name = "echo"
    description = "Echoes the input message"
    parameters = {"message": "The message to echo"}

    def execute(self, args: dict) -> ToolResult:
        return ToolResult.ok(f"Echo: {args['message']}", {"echoed": args["message"]})


def make_settings(**overrides) -> Settings:
    s = Settings()
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


# ── Happy path ───────────────────────────────────────────────────────────


def test_orchestrator_runs_with_no_tools():
    """Happy path: orchestrator completes without raising, even with no tools."""
    settings = make_settings(llm_api_key="test-key", max_iterations=5)
    memory = MemoryStore()
    tools = ToolRegistry()
    orch = Orchestrator(settings=settings, memory=memory, tools=tools)

    result = orch.run("test goal")

    assert isinstance(result, str)
    assert "test goal" in result


def test_orchestrator_executes_registered_tool():
    """Happy path: registered tool is called and result appears in synthesis."""
    settings = make_settings(llm_api_key="test-key", max_iterations=5)
    memory = MemoryStore()
    tools = ToolRegistry()
    tools.register(EchoTool())

    orch = Orchestrator(settings=settings, memory=memory, tools=tools)

    # Manually build a plan to directly test tool execution
    from agent.core import Plan, Step

    plan = Plan(goal="echo test")
    plan.steps.append(Step(id=1, description="echo", tool="echo", args={"message": "hello"}))

    results = orch.execute(plan)

    assert len(results) == 1
    assert results[0].success is True
    assert "hello" in results[0].text


def test_tool_result_envelope():
    """Happy path: ToolResult.ok and ToolResult.fail produce correct envelope."""
    ok = ToolResult.ok("success message", {"key": "value"})
    assert ok.success is True
    assert ok.text == "success message"
    assert ok.data["key"] == "value"
    assert ok.error == ""

    fail = ToolResult.fail("something went wrong")
    assert fail.success is False
    assert fail.error == "something went wrong"


# ── Failure cases ────────────────────────────────────────────────────────


def test_tool_registry_unknown_tool():
    """Failure: calling an unregistered tool returns fail result (no exception)."""
    registry = ToolRegistry()
    result = registry.execute("nonexistent_tool", {})

    assert result.success is False
    assert "nonexistent_tool" in result.error or "Unknown tool" in result.error


def test_tool_registry_missing_required_param():
    """Failure: calling a tool with missing required parameter returns fail result."""
    registry = ToolRegistry()
    registry.register(EchoTool())

    result = registry.execute("echo", {})  # missing 'message'

    assert result.success is False
    assert "message" in result.error.lower() or "validation" in result.error.lower()


def test_settings_validation_catches_missing_key():
    """Failure: settings with no API key reports validation error."""
    settings = Settings(llm_api_key="")
    errors = settings.validate()

    assert len(errors) > 0
    assert any("api_key" in e.lower() or "API_KEY" in e for e in errors)


def test_settings_validation_temperature_bounds():
    """Failure: temperature outside 0–2 is rejected."""
    settings = Settings(llm_api_key="key", llm_temperature=5.0)
    errors = settings.validate()

    assert any("temperature" in e.lower() for e in errors)


# ── Memory tier tests ────────────────────────────────────────────────────


def test_episodic_memory_records_and_retrieves():
    from agent.memory import EpisodicMemory

    mem = EpisodicMemory()
    mem.record("test_event", "something happened")
    assert len(mem) == 1
    assert mem.recent(1)[0].content == "something happened"


def test_semantic_memory_stores_and_retrieves():
    from agent.memory import SemanticMemory

    mem = SemanticMemory()
    mem.store("entity_count", 42, confidence=0.9, source="test")
    fact = mem.retrieve("entity_count")
    assert fact is not None
    assert fact.value == 42
    assert fact.confidence == 0.9


def test_working_memory_clear():
    from agent.memory import WorkingMemory

    wm = WorkingMemory()
    wm.set_goal("find the answer")
    wm.observe("first observation")
    wm.clear()
    assert wm.goal == ""
    assert wm.observations == []
