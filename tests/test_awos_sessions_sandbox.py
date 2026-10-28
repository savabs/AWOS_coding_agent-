"""Tests for awos sessions sandbox display."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import awos
from scaffold.agent.runtime_session import RuntimeSession, SessionSandbox


def _session(*, enabled: bool = False) -> RuntimeSession:
    s = RuntimeSession(
        session_id="rs_test1234567",
        goal="test",
        codebase_root=".",
    )
    s.sandbox = SessionSandbox(
        enabled=enabled,
        feature_id="feat1",
        worktree_path="/tmp/wt",
        branch="awos/feature/feat1",
    )
    return s


def test_sandbox_summary_disabled():
    assert awos._sandbox_summary(_session(enabled=False)) == ""


@patch("awos.os.path.isdir", return_value=True)
def test_sandbox_summary_enabled_exists(_mock_isdir):
    line = awos._sandbox_summary(_session(enabled=True))
    assert "feature_id=feat1" in line
    assert "path=/tmp/wt" in line
    assert "exists=True" in line


@patch("awos.os.path.isdir", return_value=False)
def test_sandbox_summary_enabled_missing_dir(_mock_isdir):
    line = awos._sandbox_summary(_session(enabled=True))
    assert "exists=False" in line
