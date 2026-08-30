"""Tests for the live-agent renderer projection and entry point."""

import inspect
from pathlib import Path

from gui.renderer_state import project_session
from gui.server import GuiHandler


def test_project_session_builds_execution_view():
    state = project_session([
        {
            "type": "session_start",
            "session_id": "rs_demo",
            "payload": {"goal": "Fix auth", "codebase_root": "/repo"},
        },
        {
            "type": "agent_tool_call",
            "session_id": "rs_demo",
            "payload": {"action": "grep", "observation": "auth.py", "success": True},
        },
        {
            "type": "task_start",
            "session_id": "rs_demo",
            "payload": {"task_id": 1, "action": "Fix auth", "file": "auth.py"},
        },
        {
            "type": "file_edit",
            "session_id": "rs_demo",
            "payload": {"path": "auth.py", "lines_added": 2},
        },
        {
            "type": "test_result",
            "session_id": "rs_demo",
            "payload": {"test_name": "test_login", "passed": True},
        },
        {
            "type": "task_complete",
            "session_id": "rs_demo",
            "payload": {"task_id": 1, "success": True, "cost_usd": 0.01},
        },
        {
            "type": "session_done",
            "session_id": "rs_demo",
            "payload": {"completed": 1, "failed": 0, "elapsed": 2.5},
        },
    ])

    assert state["session"] == {
        "session_id": "rs_demo",
        "goal": "Fix auth",
        "status": "completed",
        "codebase_root": "/repo",
        "elapsed": 2.5,
        "total_cost": 0.0,
    }
    assert len(state["timeline"]) == 7
    assert state["exploration"][0]["action"] == "grep"
    assert state["tasks"][0]["status"] == "completed"
    assert state["files"][0]["path"] == "auth.py"
    assert state["verification"][0]["passed"] is True
    assert state["parallel_task_count"] == 1


def test_project_session_keeps_parallel_tasks_isolated():
    state = project_session([
        {"type": "task_start", "session_id": "rs", "payload": {"task_id": 1, "action": "one"}},
        {"type": "task_start", "session_id": "rs", "payload": {"task_id": 2, "action": "two"}},
        {"type": "task_complete", "session_id": "rs", "payload": {"task_id": 1, "success": False, "error": "failed"}},
    ])

    tasks = {task["task_id"]: task for task in state["tasks"]}
    assert tasks["1"]["status"] == "failed"
    assert tasks["2"]["status"] == "running"
    assert state["parallel_task_count"] == 2


def test_project_session_handles_empty_and_malformed_input():
    empty = project_session([])
    assert empty["session"]["status"] == "idle"
    assert empty["timeline"] == []

    malformed = project_session([None, {"payload": {}}, "bad"])
    assert malformed["session"]["status"] == "error"
    assert len(malformed["errors"]) == 3


def test_project_session_uses_manifest_for_saved_replay():
    state = project_session([], {
        "session_id": "rs_saved",
        "goal": "Replay me",
        "status": "completed",
        "elapsed_sec": 4.0,
        "total_cost_usd": 0.02,
        "files": [{"path": "main.py", "status": "modified"}],
    })

    assert state["session"]["session_id"] == "rs_saved"
    assert state["session"]["status"] == "completed"
    assert state["session"]["elapsed"] == 4.0
    assert state["files"][0]["path"] == "main.py"


def test_renderer_entrypoint_is_served_and_contains_developer_regions():
    source = inspect.getsource(GuiHandler.do_GET)
    assert '"/renderer"' in source
    html = Path(__file__).resolve().parent.parent / "gui" / "static" / "renderer.html"
    text = html.read_text(encoding="utf-8")
    for marker in ("Live timeline", "Parallel task lanes", "Exploration and tools", "Files and verification", "/api/observer/snapshot", "/api/observer/events", "/api/observer/stream", "function apply(ev)"):
        assert marker in text


def test_renderer_browser_smoke_without_provider_calls():
    pytest = __import__("pytest")
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    from http.server import ThreadingHTTPServer
    import threading

    server = ThreadingHTTPServer(("127.0.0.1", 0), GuiHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    errors = []
    try:
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(headless=True, channel="chrome")
            except Exception:
                browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
            response = page.goto(f"http://127.0.0.1:{server.server_port}/renderer", wait_until="domcontentloaded")
            assert response is not None and response.ok
            assert page.title() == "AWOS Developer Renderer"
            assert page.locator("text=Live timeline").is_visible()
            assert page.locator("text=Parallel task lanes").is_visible()
            page.wait_for_timeout(300)
            assert errors == []
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
