import inspect
import json
from pathlib import Path

from gui.server import resolve_observer_session
from scaffold.agent.observer_protocol import build_event


def write_event(root: Path, session_id: str, *, chat_id=None, trace_id=None):
    target = root / session_id
    target.mkdir(parents=True)
    event = build_event(
        event_type="session_start",
        session_id=session_id,
        payload={"goal": "demo"},
        chat_id=chat_id,
        trace_id=trace_id,
    )
    (target / "events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")


def test_chat_orchestrator_compatibility_arguments_exist():
    from scaffold.agent.orchestrator import Orchestrator

    parameters = inspect.signature(Orchestrator.execute_feature).parameters
    for name in ("extra_context", "cancel_event", "on_runtime_session", "chat_id", "trace_id"):
        assert name in parameters


def test_resolver_matches_explicit_trace(tmp_path):
    write_event(tmp_path, "rs_one", chat_id="chat_1", trace_id="trace_1")
    result = resolve_observer_session(trace_id="trace_1", gui_root=tmp_path)
    assert result["status"] == "resolved"
    assert result["session_id"] == "rs_one"


def test_resolver_does_not_guess_when_missing(tmp_path):
    result = resolve_observer_session(chat_id="chat_missing", gui_root=tmp_path)
    assert result["status"] == "not_found"


def test_resolver_reports_ambiguity(tmp_path):
    write_event(tmp_path, "rs_one", trace_id="trace_shared")
    write_event(tmp_path, "rs_two", trace_id="trace_shared")
    result = resolve_observer_session(trace_id="trace_shared", gui_root=tmp_path)
    assert result["status"] == "ambiguous"
    assert result["session_ids"] == ["rs_one", "rs_two"]
