"""Tests for awos sessions CLI handlers."""

from __future__ import annotations

import argparse
from types import SimpleNamespace

import pytest

from scaffold.agent.runtime_session import RuntimeSessionStore, SessionStatus


@pytest.fixture
def sessions_dir(tmp_path, monkeypatch):
    d = tmp_path / ".awos" / "sessions"
    d.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    return d


@pytest.fixture
def store(sessions_dir):
    return RuntimeSessionStore(sessions_dir=str(sessions_dir))


def _import_awos():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "awos_cli",
        Path(__file__).resolve().parents[1] / "awos.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestSessionsCLI:
    def test_list_empty(self, capsys):
        awos = _import_awos()
        awos.cmd_sessions_list(SimpleNamespace(json=False))
        assert "No runtime sessions" in capsys.readouterr().out

    def test_list_shows_session(self, store, capsys):
        s = store.create("build auth")
        store.finalize(s, SessionStatus.PAUSED)
        awos = _import_awos()
        awos.cmd_sessions_list(SimpleNamespace(json=False))
        out = capsys.readouterr().out
        assert s.session_id in out
        assert "paused" in out

    def test_show_session(self, store, capsys):
        s = store.create("show me")
        awos = _import_awos()
        awos.cmd_sessions_show(SimpleNamespace(session_id=s.session_id, json=False))
        out = capsys.readouterr().out
        assert "show me" in out
        assert s.session_id in out

    def test_show_missing(self, capsys):
        awos = _import_awos()
        with pytest.raises(SystemExit) as exc:
            awos.cmd_sessions_show(
                SimpleNamespace(session_id="rs_" + "a" * 12, json=False)
            )
        assert exc.value.code == 1

    def test_fork_prints_new_id(self, store, capsys):
        parent = store.create("parent")
        store.checkpoint(parent, completed_task_id=1, total_tasks=3)
        store.finalize(parent, SessionStatus.PAUSED)
        awos = _import_awos()
        awos.cmd_sessions_fork(
            SimpleNamespace(session_id=parent.session_id, goal=None, json=False)
        )
        out = capsys.readouterr().out
        assert "Forked" in out
        assert parent.session_id in out

    def test_list_json(self, store, capsys):
        store.create("json goal")
        awos = _import_awos()
        awos.cmd_sessions_list(SimpleNamespace(json=True))
        import json

        data = json.loads(capsys.readouterr().out)
        assert len(data) == 1
        assert data[0]["goal"] == "json goal"
