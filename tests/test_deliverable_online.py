"""Automated online E2E tests — planner-owned create_file path."""

import json

import pytest

from scaffold.agent.deliverable_e2e import (
    _bootstrap_e2e_env,
    install_mock_create_executor,
    install_mock_planner,
    run_all_checks,
    run_http_agent_create_check,
    start_test_gui_server,
)


@pytest.fixture
def e2e_workspace(tmp_path, monkeypatch):
    _bootstrap_e2e_env()
    (tmp_path / "docs").mkdir()
    (tmp_path / "run_awos_demo.py").write_text("# demo\n", encoding="utf-8")
    install_mock_create_executor(monkeypatch)
    install_mock_planner(monkeypatch)
    return tmp_path


def test_deliverable_e2e_suite(e2e_workspace, monkeypatch):
    report = run_all_checks(workspace=e2e_workspace, use_mock=False, monkeypatch=monkeypatch)
    assert report.ok, json.dumps(report.to_dict(), indent=2)


def test_http_sse_agent_creates_file(e2e_workspace, monkeypatch):
    from scaffold.agent.deliverable_e2e import _free_port

    install_mock_create_executor(monkeypatch)
    install_mock_planner(monkeypatch)
    port = _free_port()
    server = start_test_gui_server(e2e_workspace, port)
    try:
        info = run_http_agent_create_check(
            f"http://127.0.0.1:{port}",
            e2e_workspace,
            "create docs/http_only_test.md about automated checks",
        )
        assert info["files"]
        assert (e2e_workspace / info["files"][0]).is_file()
    finally:
        server.shutdown()
