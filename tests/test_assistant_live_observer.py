"""Focused tests for the assistant live observer event contract."""

import json
from pathlib import Path

import pytest

from scaffold.agent.gui_chat import GuiChatService, GuiChatStore
from scaffold.agent.gui_events import GuiEvent, GuiEventBus
from scaffold.agent.observer_protocol import (
    EventEnvelopeError,
    build_event,
    parse_event_line,
    project_assistant_event,
    project_assistant_events,
    validate_envelope,
)


def test_build_event_has_versioned_correlated_safe_envelope():
    event = build_event(
        event_type="task_start",
        session_id="rs_current",
        payload={"action": "inspect", "file": "src/app.py"},
        sequence=7,
        source="agent",
        chat_id="chat_current",
        task_id="task_1",
        trace_id="trace_current",
    )

    assert event["schema_version"] == 1
    assert event["event_id"].startswith("evt_")
    assert event["sequence"] == 7
    assert event["emitted_at"].endswith("Z")
    assert event["source"] == "agent"
    assert event["type"] == "task_start"
    assert event["session_id"] == "rs_current"
    assert event["chat_id"] == "chat_current"
    assert event["task_id"] == "task_1"
    assert event["trace_id"] == "trace_current"
    assert event["visibility"] == "assistant_safe"


def test_gui_event_preserves_legacy_timestamp_while_emitting_contract():
    event = GuiEvent(
        type="session_start",
        session_id="rs_legacy",
        timestamp="2026-07-28T10:00:00Z",
        payload={"goal": "observe"},
    ).to_dict()

    assert event["schema_version"] == 1
    assert event["emitted_at"] == "2026-07-28T10:00:00Z"
    assert event["timestamp"] == event["emitted_at"]
    assert event["payload"] == {"goal": "observe"}


def test_gui_event_bus_writes_valid_envelopes(tmp_path):
    bus = GuiEventBus("rs_contract", gui_root=tmp_path)
    event = bus.emit("session_start", {"goal": "trace the current run"})

    persisted = json.loads((tmp_path / "rs_contract" / "events.jsonl").read_text())
    assert persisted["event_id"] == event.event_id
    assert persisted["schema_version"] == 1
    assert persisted["session_id"] == "rs_contract"
    assert validate_envelope(persisted)["type"] == "session_start"


def test_event_ids_and_sequences_survive_bus_reopen(tmp_path):
    first_bus = GuiEventBus("rs_cursor", gui_root=tmp_path)
    first = [first_bus.emit("task_start"), first_bus.emit("agent_tool_call")]

    second_bus = GuiEventBus("rs_cursor", gui_root=tmp_path)
    third = second_bus.emit("test_result")
    persisted = [
        json.loads(line)
        for line in (tmp_path / "rs_cursor" / "events.jsonl").read_text().splitlines()
    ]

    assert [event.sequence for event in first] == [1, 2]
    assert third.sequence == 3
    assert [event["sequence"] for event in persisted] == [1, 2, 3]
    assert len({event["event_id"] for event in persisted}) == 3


def test_correlation_fields_join_chat_runtime_and_task_events(tmp_path):
    bus = GuiEventBus(
        "rs_correlated",
        gui_root=tmp_path,
        chat_id="chat_current",
        trace_id="trace_current",
    )
    start = bus.emit("session_start")
    task = bus.emit_task_start(4, "inspect", "app.py", "model", "reason")

    assert start.to_dict()["chat_id"] == "chat_current"
    assert start.to_dict()["trace_id"] == "trace_current"
    assert task.to_dict()["session_id"] == "rs_correlated"
    assert task.to_dict()["task_id"] == "4"
    assert task.to_dict()["trace_id"] == "trace_current"


def test_chat_start_event_has_stable_chat_trace_context(tmp_path):
    service = GuiChatService(codebase_root=str(tmp_path), store=GuiChatStore(tmp_path / "chats"))
    stream = service.handle_message_stream("inspect the current run")
    start = next(stream)
    stream.close()

    assert start["event"] == "start"
    assert start["data"]["chat_id"].startswith("chat_")
    assert start["data"]["trace_id"].startswith("trace_")


def test_malformed_event_is_rejected_without_parser_crash():
    malformed = {
        "schema_version": 1,
        "event_id": "not-an-event-id",
        "sequence": 1,
        "emitted_at": "2026-07-28T10:00:00Z",
        "source": "agent",
        "type": "task_start",
        "session_id": "rs_bad",
        "payload": {},
        "visibility": "assistant_safe",
    }

    with pytest.raises(EventEnvelopeError):
        validate_envelope(malformed)
    assert parse_event_line(json.dumps(malformed)) is None
    assert parse_event_line("{truncated") is None


def test_envelope_rejects_invalid_source_visibility_and_oversized_payload():
    with pytest.raises(EventEnvelopeError, match="source"):
        build_event(
            event_type="task_start",
            session_id="rs_bad",
            source="unknown",
        )

    with pytest.raises(EventEnvelopeError, match="visibility"):
        build_event(
            event_type="task_start",
            session_id="rs_bad",
            visibility="private",
        )

    with pytest.raises(EventEnvelopeError, match="payload"):
        build_event(
            event_type="task_start",
            session_id="rs_bad",
            payload={"output": "x" * (32 * 1024)},
        )


def test_internal_thinking_is_not_assistant_safe(tmp_path):
    event = GuiEventBus("rs_private", gui_root=tmp_path)
    emitted = event.emit_agent_thinking("internal diagnostic", turn=2).to_dict()
    assert emitted["visibility"] == "developer_only"
    assert project_assistant_event(emitted) is None


def test_observer_cli_parser_is_read_only_and_supports_cursor_filters():
    from awos import build_parser

    parser = build_parser()
    snapshot = parser.parse_args(["observe", "snapshot", "--session", "rs_cli"])
    events = parser.parse_args(
        ["observe", "events", "--session", "rs_cli", "--after", "evt_1", "--type", "test_result", "--follow"]
    )
    assert snapshot.command == "observe"
    assert snapshot.observe_cmd == "snapshot"
    assert snapshot.session_id == "rs_cli"
    assert events.observe_cmd == "events"
    assert events.after == "evt_1"
    assert events.event_type == ["test_result"]
    assert events.follow is True


def test_snapshot_projects_current_run_and_matching_ui_action(tmp_path, monkeypatch):
    import gui.server as server

    monkeypatch.setattr(server, "GUI_ROOT", tmp_path / "gui")
    run_bus = GuiEventBus("rs_snapshot", gui_root=tmp_path / "gui", chat_id="chat_snapshot", trace_id="trace_snapshot")
    run_bus.emit_session_start("Fix the current issue", "/repo")
    run_bus.emit_plan_generated(1, [{"task_id": 1, "action": "inspect"}])
    run_bus.emit_task_start(1, "inspect", "app.py", "model", "reason")
    run_bus.emit_file_edit("app.py", "modified", 2, 1)
    run_bus.emit_test_result("test_app", True, 0.2)
    run_bus.emit_task_complete(1, True, 0.01, 1)
    run_bus.emit_session_done(1, 0, 0.01, 1.0)
    server.emit_ui_observer_event(
        "chat_submitted",
        {"mode": "agent", "has_message": True, "message_chars": 10},
        chat_id="chat_snapshot",
        trace_id="trace_snapshot",
    )

    snapshot = server.build_observer_snapshot(
        "rs_snapshot",
        gui_root=tmp_path / "gui",
        runtime_sessions_dir=tmp_path / "sessions",
    )
    assert snapshot["status"] == "completed"
    assert snapshot["session"]["goal"] == "Fix the current issue"
    assert snapshot["current_user_action"]["type"] == "chat_submitted"
    assert snapshot["current_chat"] == {"chat_id": "chat_snapshot", "trace_id": "trace_snapshot"}
    assert snapshot["current_task"]["status"] == "completed"
    assert snapshot["plan"]["n_tasks"] == 1
    assert snapshot["verification"][0]["passed"] is True
    assert snapshot["cursor"].startswith("evt_")


def test_snapshot_returns_idle_without_inventing_a_current_session(tmp_path):
    from gui.server import build_observer_snapshot

    snapshot = build_observer_snapshot(
        gui_root=tmp_path / "gui",
        runtime_sessions_dir=tmp_path / "sessions",
    )
    assert snapshot["status"] == "idle"
    assert snapshot["session"] is None
    assert snapshot["current_task"] is None


def test_observer_sse_frame_has_id_and_named_event():
    from gui.server import format_observer_sse

    event = build_event(event_type="test_result", session_id="rs_sse", sequence=1, payload={"passed": True})
    frame = format_observer_sse(event)
    assert frame.startswith(f"id: {event['event_id']}\nevent: observer\n")
    assert '"type":"test_result"' in frame
    assert frame.endswith("\n\n")


def test_cursor_replay_returns_only_later_safe_events(tmp_path):
    from gui.server import read_observer_events

    bus = GuiEventBus("rs_replay", gui_root=tmp_path)
    first = bus.emit("task_start", {"task_id": 1})
    bus.emit_agent_thinking("internal detail")
    second = bus.emit("test_result", {"passed": True})
    with (tmp_path / "rs_replay" / "events.jsonl").open("a") as stream:
        stream.write("{truncated observer line\n")

    replay = read_observer_events("rs_replay", after=first.event_id, limit=10, gui_root=tmp_path)
    assert [event["event_id"] for event in replay["events"]] == [second.event_id]
    assert replay["gap"] is False
    assert replay["malformed_lines"] == 1

    gap = read_observer_events("rs_replay", after="evt_missing", gui_root=tmp_path)
    assert gap["gap"] is True
    assert gap["reason"] == "unknown_cursor"
    assert gap["events"] == []

    filtered = read_observer_events("rs_replay", event_types={"test_result"}, gui_root=tmp_path)
    assert [event["type"] for event in filtered["events"]] == ["test_result"]
    assert filtered["next_cursor"] == second.event_id


def test_ui_action_helper_persists_correlated_ui_event(tmp_path, monkeypatch):
    import gui.server as server

    monkeypatch.setattr(server, "GUI_ROOT", tmp_path / "gui")
    event = server.emit_ui_observer_event(
        "chat_submitted",
        {"mode": "agent", "has_message": True, "message_chars": 12},
        chat_id="chat_ui",
        trace_id="trace_ui",
    )

    assert event["source"] == "ui"
    assert event["chat_id"] == "chat_ui"
    assert event["trace_id"] == "trace_ui"
    assert json.loads((tmp_path / "gui" / "ui" / "events.jsonl").read_text())["type"] == "chat_submitted"


def test_chat_ui_emits_semantic_actions_without_keystroke_logging():
    chat_source = (Path(__file__).resolve().parents[1] / "gui" / "static" / "chat.js").read_text()
    server_source = (Path(__file__).resolve().parents[1] / "gui" / "server.py").read_text()

    for marker in ("app_opened", "view_changed", "chat_submitted", "session_selected", "task_control"):
        assert marker in chat_source
    assert "/api/observer/ui-event" in chat_source
    assert "/api/observer/ui-event" in server_source
    assert "message_chars: message.length" in chat_source
    assert "has_message: true" in chat_source


def test_electron_agent_entrypoint_emits_correlated_semantic_actions():
    agent_source = (Path(__file__).resolve().parents[1] / "gui" / "static" / "agent.html").read_text()

    for marker in ("app_opened", "view_changed", "chat_submitted", "task_control"):
        assert marker in agent_source
    assert "/api/observer/ui-event" in agent_source
    assert "observerChatId" in agent_source
    assert "observerTraceId" in agent_source
    assert "message_chars: question.length" in agent_source
    assert "keydown" in agent_source


def test_assistant_projection_redacts_nested_credentials():
    event = build_event(
        event_type="agent_tool_call",
        session_id="rs_safe",
        payload={
            "action": "request",
            "api_key": "sk-live-secret",
            "nested": {"Authorization": "Bearer very-secret"},
            "output": "token=inline-secret",
        },
    )

    safe = project_assistant_event(event)
    assert safe is not None
    assert safe["visibility"] == "redacted"
    assert safe["payload"]["api_key"] == "[REDACTED]"
    assert safe["payload"]["nested"]["Authorization"] == "[REDACTED]"
    assert "inline-secret" not in safe["payload"]["output"]


def test_assistant_projection_drops_unknown_and_bounds_large_events():
    internal = build_event(
        event_type="internal_diagnostic",
        session_id="rs_safe",
        payload={"detail": "not for the assistant"},
    )
    large = build_event(
        event_type="test_result",
        session_id="rs_safe",
        payload={"output": "x" * (8 * 1024)},
    )

    assert project_assistant_event(internal) is None
    projected = project_assistant_event(large)
    assert projected is not None
    assert projected["visibility"] == "redacted"
    assert projected["payload"]["omitted"] is True
    assert project_assistant_events([internal, large, {"bad": "line"}]) == [projected]
