import os
import tempfile
from pathlib import Path
from gui.observer_control import generate_token, load_token, validate_token, append_audit, read_audit_lines
from scaffold.agent.observer_protocol import build_event, project_assistant_event
from scaffold.agent.gui_events import UIEventType
from gui.observer_client import ControlClientError, send_control, verify_control_result


class _Response:
    def __init__(self, payload):
        import json
        self.payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self.payload


def test_control_client_builds_authenticated_request_and_verifies_fill():
    captured = {}

    def opener(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = request.data
        captured["token"] = request.get_header("X-awos-control-token")
        captured["timeout"] = timeout
        return _Response({
            "status": "ok",
            "action": "fill",
            "request_id": "ctl_test",
            "result": {"accepted": True, "target": "prompt", "value_chars": 5},
        })

    response = send_control("fill", target="prompt", value="hello", token="secret", opener=opener)
    verified = verify_control_result(response, "fill", target="prompt", value="hello")
    assert verified["status"] == "ok"
    assert captured["url"] == "http://127.0.0.1:8766/api/control"
    assert captured["token"] == "secret"
    assert captured["timeout"] == 5
    assert b'"action": "fill"' in captured["body"]


def test_control_client_rejects_unverified_navigation_result():
    response = {"status": "ok", "action": "navigate", "result": {"route": "/agent"}}
    try:
        verify_control_result(response, "navigate", target="/renderer")
    except ControlClientError as exc:
        assert "navigation" in str(exc)
    else:
        raise AssertionError("mismatched navigation should be rejected")


def test_token_generate_and_validate(tmp_path):
    out = tmp_path / "observer_token.json"
    obj = generate_token(path=str(out), ttl_seconds=2)
    assert "token" in obj
    loaded = load_token(path=str(out))
    assert loaded["token"] == obj["token"]
    assert validate_token(loaded["token"], path=str(out)) is True


def test_control_events_are_ui_types_and_assistant_safe():
    assert UIEventType.CONTROL_REQUESTED == "control_requested"
    assert UIEventType.CONTROL_COMPLETED == "control_completed"
    assert UIEventType.CONTROL_REJECTED == "control_rejected"
    for event_type in (UIEventType.CONTROL_REQUESTED, UIEventType.CONTROL_COMPLETED, UIEventType.CONTROL_REJECTED):
        event = build_event(
            event_type=event_type,
            session_id="ui",
            source="ui",
            payload={"request_id": "ctl_test", "action": "status"},
        )
        projected = project_assistant_event(event)
        assert projected is not None
        assert projected["type"] == event_type


def test_ui_observer_bus_keeps_monotonic_sequences(tmp_path, monkeypatch):
    import gui.server as server

    monkeypatch.setattr(server, "GUI_ROOT", tmp_path)
    monkeypatch.setattr(server, "_ui_observer_bus", None)
    first = server.emit_ui_observer_event("control_requested", {"action": "status"})
    second = server.emit_ui_observer_event("control_completed", {"action": "status"})
    assert second["sequence"] == first["sequence"] + 1


def test_audit_append_and_read(tmp_path):
    logdir = tmp_path / "control_log"
    entry = {"cmd": "test", "ok": True}
    p = append_audit(entry, log_dir=str(logdir))
    assert Path(p).exists()
    lines = read_audit_lines(log_dir=str(logdir))
    assert any(l.get("cmd") == "test" or l.get("command") == "test" or l.get("cmd") == "test" for l in lines)
