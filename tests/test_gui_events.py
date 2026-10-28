"""
Tests for gui_events.py — EventType constants and GuiEventBus emit helpers.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from scaffold.agent.gui_events import EventType, GuiEventBus, GuiEvent


class TestEventType:
    def test_all_constants_defined(self):
        """All 11 core event types plus idle/error exist."""
        expected = {
            "session_start",
            "codebase_indexed",
            "plan_generated",
            "model_routed",
            "task_start",
            "file_edit",
            "test_result",
            "task_complete",
            "session_done",
            "agent_thinking",
            "agent_tool_call",
            "idle",
            "error",
        }
        assert EventType.all() == expected

    def test_no_duplicates(self):
        """No two constants share the same value."""
        values = [
            v for k, v in vars(EventType).items()
            if not k.startswith("_") and isinstance(v, str)
        ]
        assert len(values) == len(set(values))

    def test_all_returned_by_classmethod(self):
        """EventType.all() returns every str constant."""
        collected = {
            v for k, v in vars(EventType).items()
            if not k.startswith("_") and isinstance(v, str)
        }
        assert EventType.all() == collected


class TestGuiEventBusEmitHelpers:
    @pytest.fixture
    def bus(self):
        """Create a GuiEventBus with a temp dir for isolation."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bus = GuiEventBus("test_session", "test goal", gui_root=root / ".awos" / "gui")
            yield bus

    def _read_events(self, bus: GuiEventBus) -> list[dict]:
        """Read all events from the bus's event file."""
        if not bus._events_path.exists():
            return []
        events = []
        for line in bus._events_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
        return events

    def test_emit_session_start(self, bus):
        event = bus.emit_session_start("test goal", "/tmp/repo")
        assert event.type == EventType.SESSION_START
        assert event.payload["goal"] == "test goal"
        assert event.payload["codebase_root"] == "/tmp/repo"
        events = self._read_events(bus)
        assert len(events) == 1
        assert events[0]["type"] == EventType.SESSION_START

    def test_emit_codebase_indexed(self, bus):
        event = bus.emit_codebase_indexed(42, 1.5)
        assert event.payload["n_chunks"] == 42
        assert event.payload["elapsed"] == 1.5

    def test_emit_plan_generated(self, bus):
        tasks = [{"task_id": 1, "action": "fix test", "complexity": 3}]
        event = bus.emit_plan_generated(1, tasks)
        assert event.payload["n_tasks"] == 1
        assert event.payload["tasks"] == tasks

    def test_emit_model_routed(self, bus):
        event = bus.emit_model_routed(
            model_name="DeepSeek V4 Flash",
            tier="T2",
            reason="Best cost/quality for CI tasks",
            cost_estimate=0.001,
            provider="deepseek",
        )
        assert event.payload["model_name"] == "DeepSeek V4 Flash"
        assert event.payload["tier"] == "T2"
        assert event.payload["cost_estimate"] == 0.001
        assert event.payload["provider"] == "deepseek"

    def test_emit_task_start(self, bus):
        event = bus.emit_task_start(
            task_id=1,
            action="Fix test_auth_login",
            file="auth.py",
            model_name="DeepSeek V4 Flash",
            model_reason="Standard logic fix",
        )
        assert event.payload["task_id"] == 1
        assert event.payload["action"] == "Fix test_auth_login"
        assert event.payload["file"] == "auth.py"
        assert event.payload["model_name"] == "DeepSeek V4 Flash"

    def test_emit_file_edit(self, bus):
        event = bus.emit_file_edit(
            path="auth.py",
            status="modified",
            lines_added=3,
            lines_removed=1,
            diff="@@ -42,6 +42,9 @@\n+if not x:\n",
        )
        assert event.payload["path"] == "auth.py"
        assert event.payload["lines_added"] == 3
        assert event.payload["diff"] != ""

    def test_emit_test_result_pass(self, bus):
        event = bus.emit_test_result(
            test_name="test_auth_login",
            passed=True,
            duration=1.2,
        )
        assert event.payload["test_name"] == "test_auth_login"
        assert event.payload["passed"] is True
        assert event.payload["duration"] == 1.2

    def test_emit_test_result_fail(self, bus):
        event = bus.emit_test_result(
            test_name="test_auth_fail",
            passed=False,
            duration=0.5,
            output="AssertionError: expected True got False",
        )
        assert event.payload["passed"] is False
        assert event.payload["output"] != ""

    def test_emit_task_complete_success(self, bus):
        event = bus.emit_task_complete(
            task_id=1,
            success=True,
            cost_usd=0.0012,
            n_edits=1,
        )
        assert event.payload["success"] is True
        assert event.payload["cost_usd"] == 0.0012

    def test_emit_task_complete_failure(self, bus):
        event = bus.emit_task_complete(
            task_id=2,
            success=False,
            cost_usd=0.005,
            n_edits=0,
            error="Test still failing after 3 retries",
        )
        assert event.payload["success"] is False
        assert event.payload["error"] != ""

    def test_emit_session_done(self, bus):
        event = bus.emit_session_done(
            completed=5,
            failed=1,
            total_cost=0.0056,
            elapsed=49.7,
        )
        assert event.payload["completed"] == 5
        assert event.payload["failed"] == 1
        assert event.payload["total_cost"] == 0.0056
        assert event.payload["elapsed"] == 49.7

    def test_multiple_events_append(self, bus):
        bus.emit_session_start("goal", "/tmp")
        bus.emit_task_start(1, "fix", "x.py", "model", "reason")
        bus.emit_task_complete(1, True, 0.001, 1)
        events = self._read_events(bus)
        assert len(events) == 3
        assert events[0]["type"] == EventType.SESSION_START
        assert events[1]["type"] == EventType.TASK_START
        assert events[2]["type"] == EventType.TASK_COMPLETE

    def test_emit_returns_gui_event(self, bus):
        event = bus.emit_session_start("g", "/r")
        assert isinstance(event, GuiEvent)
        assert event.session_id == "test_session"

    def test_all_emit_helpers_write_to_file(self, bus):
        """Every emit helper writes exactly one line to events.jsonl."""
        bus.emit_session_start("g", "/r")
        bus.emit_codebase_indexed(10, 0.5)
        bus.emit_plan_generated(3, [])
        bus.emit_model_routed("m", "T1", "r", 0.0, "p")
        bus.emit_task_start(1, "a", "f", "m", "r")
        bus.emit_file_edit("f", "modified", 1, 0)
        bus.emit_test_result("t", True, 0.1)
        bus.emit_task_complete(1, True, 0.0, 1)
        bus.emit_session_done(1, 0, 0.0, 1.0)
        bus.emit_agent_thinking("I should check the auth module", 1)
        bus.emit_agent_tool_call("grep_search", {"pattern": "login"}, "Found 3 matches", True, 120.0, 1)
        events = self._read_events(bus)
        assert len(events) == 11
        types = [e["type"] for e in events]
        assert all(t in EventType.all() for t in types)

    def test_emit_agent_thinking(self, bus):
        event = bus.emit_agent_thinking("I need to check the auth module first", turn=1)
        assert event.payload["thought"] == "I need to check the auth module first"
        assert event.payload["turn"] == 1

    def test_emit_agent_tool_call_success(self, bus):
        event = bus.emit_agent_tool_call(
            action="grep_search",
            action_input={"pattern": "login"},
            observation="3 results found",
            success=True,
            latency_ms=150.0,
            turn=2,
        )
        assert event.payload["action"] == "grep_search"
        assert event.payload["success"] is True
        assert event.payload["latency_ms"] == 150.0

    def test_emit_agent_tool_call_failure(self, bus):
        event = bus.emit_agent_tool_call(
            action="edit_file",
            action_input={"path": "x.py"},
            observation="File not found",
            success=False,
            latency_ms=50.0,
            turn=3,
        )
        assert event.payload["success"] is False
        assert "File not found" in event.payload["observation"]
