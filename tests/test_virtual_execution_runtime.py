"""Tests for VirtualExecutionRuntime — session-scoped git worktree sandbox."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scaffold.agent.runtime_session import (
    RuntimeSessionStore,
    SessionSandbox,
    SessionStatus,
)
from scaffold.agent.virtual_execution_runtime import (
    VirtualExecutionRuntime,
    feature_id_for_session,
    is_git_repository,
    should_sync_repo_file,
    sync_worktree_from_repo,
    worktree_enabled,
)


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "AWOS Test",
            "GIT_AUTHOR_EMAIL": "awos@test.local",
            "GIT_COMMITTER_NAME": "AWOS Test",
            "GIT_COMMITTER_EMAIL": "awos@test.local",
        }
    )
    return env


def _init_git_repo(path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=path,
        check=True,
        capture_output=True,
        env=_git_env(),
    )


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    d = tmp_path / ".awos" / "sessions"
    d.mkdir(parents=True)
    return RuntimeSessionStore(sessions_dir=str(d))


class TestHelpers:
    def test_worktree_enabled(self, monkeypatch):
        monkeypatch.delenv("AWOS_USE_WORKTREE", raising=False)
        assert worktree_enabled() is False
        monkeypatch.setenv("AWOS_USE_WORKTREE", "true")
        assert worktree_enabled() is True

    def test_feature_id_from_session(self, store):
        s = store.create("goal")
        assert feature_id_for_session(s) == s.session_id[3:]

    def test_is_git_repository_false(self, tmp_path):
        assert is_git_repository(str(tmp_path)) is False


class TestWorktreeSync:
    def test_should_sync_repo_file_skips_awos(self):
        assert should_sync_repo_file(Path(".awos/state.json")) is False
        assert should_sync_repo_file(Path("scaffold/agent/foo.py")) is True

    def test_sync_worktree_from_repo_copies_untracked(self, tmp_path):
        repo = tmp_path / "repo"
        wt = tmp_path / "repo" / ".awos" / "worktrees" / "abc"
        wt.mkdir(parents=True)
        src = repo / "scaffold" / "agent"
        src.mkdir(parents=True)
        untracked = src / "new_module.py"
        untracked.write_text("x = 1\n", encoding="utf-8")

        n = sync_worktree_from_repo(str(repo), str(wt))
        assert n == 1
        assert (wt / "scaffold" / "agent" / "new_module.py").read_text() == "x = 1\n"

    def test_sync_skips_existing_worktree_files(self, tmp_path):
        repo = tmp_path / "repo"
        wt = tmp_path / "wt"
        repo.mkdir()
        wt.mkdir()
        (repo / "a.py").write_text("repo\n", encoding="utf-8")
        (wt / "a.py").write_text("wt\n", encoding="utf-8")

        n = sync_worktree_from_repo(str(repo), str(wt))
        assert n == 0
        assert (wt / "a.py").read_text() == "wt\n"


class TestVirtualExecutionRuntime:
    def test_enter_disabled_returns_repo(self, store, tmp_path, monkeypatch):
        monkeypatch.delenv("AWOS_USE_WORKTREE", raising=False)
        session = store.create("goal", codebase_root=str(tmp_path))
        vr = VirtualExecutionRuntime(str(tmp_path), session, store)
        assert vr.enter() == str(tmp_path.resolve())
        assert vr.active is False

    def test_enter_not_git_repo(self, store, tmp_path, monkeypatch):
        monkeypatch.setenv("AWOS_USE_WORKTREE", "true")
        session = store.create("goal", codebase_root=str(tmp_path))
        vr = VirtualExecutionRuntime(str(tmp_path), session, store)
        path = vr.enter()
        assert path == str(tmp_path.resolve())
        assert session.sandbox.enabled is False

    @patch("scaffold.agent.virtual_execution_runtime.WorktreeManager")
    @patch("scaffold.agent.virtual_execution_runtime.sync_worktree_from_repo")
    def test_enter_creates_worktree(self, mock_sync, mock_wm_cls, store, tmp_path, monkeypatch):
        monkeypatch.setenv("AWOS_USE_WORKTREE", "true")
        _init_git_repo(tmp_path)
        session = store.create("build feature", codebase_root=str(tmp_path))

        mock_wm = MagicMock()
        mock_wm.create_worktree.return_value = str(tmp_path / ".awos" / "worktrees" / "abc")
        mock_wm._branch_name.return_value = "awos/feature/abc"
        mock_wm_cls.return_value = mock_wm
        mock_sync.return_value = 2

        vr = VirtualExecutionRuntime(str(tmp_path), session, store)
        wt = vr.enter()

        assert vr.active is True
        assert "worktrees" in wt
        mock_wm.create_worktree.assert_called_once()
        mock_sync.assert_called_once()
        loaded = store.load(session.session_id)
        assert loaded.sandbox.enabled is True
        assert loaded.sandbox.worktree_path == wt

    @patch("scaffold.agent.virtual_execution_runtime.WorktreeManager")
    def test_enter_reattach_existing(self, mock_wm_cls, store, tmp_path, monkeypatch):
        monkeypatch.setenv("AWOS_USE_WORKTREE", "true")
        _init_git_repo(tmp_path)
        wt_dir = tmp_path / ".awos" / "worktrees" / "feat1"
        wt_dir.mkdir(parents=True)

        session = store.create("goal", codebase_root=str(tmp_path))
        session.sandbox = SessionSandbox(
            enabled=True,
            feature_id="feat1",
            worktree_path=str(wt_dir),
            branch="awos/feature/feat1",
        )
        store.save(session)

        mock_wm = MagicMock()
        mock_wm_cls.return_value = mock_wm

        vr = VirtualExecutionRuntime(str(tmp_path), session, store)
        path = vr.enter()

        assert path == str(wt_dir.resolve())
        mock_wm.register_worktree.assert_called_once_with("feat1", str(wt_dir))
        mock_wm.create_worktree.assert_not_called()

    @patch("scaffold.agent.virtual_execution_runtime.WorktreeManager")
    def test_finalize_completed_auto_merge(self, mock_wm_cls, store, tmp_path, monkeypatch):
        monkeypatch.setenv("AWOS_USE_WORKTREE", "true")
        monkeypatch.setenv("AWOS_WORKTREE_AUTO_MERGE", "true")
        session = store.create("goal", codebase_root=str(tmp_path))
        vr = VirtualExecutionRuntime(str(tmp_path), session, store)
        vr.wm = mock_wm_cls.return_value
        vr.feature_id = "feat9"
        vr.active = True

        mock_wm_cls.return_value.commit_worktree.return_value = "sha123"
        mock_wm_cls.return_value.merge_to_main.return_value = True

        summary = vr.finalize(SessionStatus.COMPLETED)

        assert summary["commit"] == "sha123"
        assert summary["merged"] is True
        assert summary["cleaned_up"] is True
        loaded = store.load(session.session_id)
        assert loaded.sandbox.enabled is False

    @patch("scaffold.agent.virtual_execution_runtime.WorktreeManager")
    def test_finalize_paused_preserves_worktree(self, mock_wm_cls, store, tmp_path, monkeypatch):
        session = store.create("goal", codebase_root=str(tmp_path))
        vr = VirtualExecutionRuntime(str(tmp_path), session, store)
        vr.wm = mock_wm_cls.return_value
        vr.feature_id = "feat9"
        vr.active = True

        summary = vr.finalize(SessionStatus.PAUSED)

        assert summary.get("worktree_preserved") is True
        mock_wm_cls.return_value.commit_worktree.assert_not_called()
