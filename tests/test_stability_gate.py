"""Tests for StabilityGate — auto-detection of test suite health."""

from __future__ import annotations

import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scaffold.agent.stability_gate import StabilityGate


@pytest.fixture
def tmp_root(tmp_path):
    return tmp_path


@pytest.fixture
def gate(tmp_root):
    return StabilityGate(
        project_root=str(tmp_root),
        env_file=".env",
        runs=3,
        min_pass=50,
        variance_tolerance=2,
    )


def _mock_runs(gate, counts):
    """Patch _run_once to return the given sequence of (count, "") pairs."""
    sequence = iter([(c, "") for c in counts])
    gate._run_once = lambda: next(sequence)


# ── TestShouldCheck ───────────────────────────────────────────────────────────

class TestShouldCheck:
    def test_fires_at_boundary(self, gate):
        assert gate.should_check(20) is True

    def test_not_between_boundaries(self, gate):
        assert gate.should_check(7) is False

    def test_zero_never(self, gate):
        assert gate.should_check(0) is False

    def test_custom_interval(self, tmp_root):
        g = StabilityGate(project_root=str(tmp_root), check_every=5)
        assert g.should_check(5) is True
        assert g.should_check(10) is True
        assert g.should_check(6) is False


# ── TestStableResult ──────────────────────────────────────────────────────────

class TestStableResult:
    def test_stable_all_same(self, gate, tmp_root):
        _mock_runs(gate, [100, 100, 100])
        stable, reason = gate.run(verbose=False)
        assert stable is True
        assert "Stable" in reason

    def test_stable_within_variance(self, gate, tmp_root):
        _mock_runs(gate, [100, 101, 100])
        stable, reason = gate.run(verbose=False)
        assert stable is True

    def test_unstable_too_much_variance(self, gate, tmp_root):
        _mock_runs(gate, [100, 95, 100])
        stable, reason = gate.run(verbose=False)
        assert stable is False
        assert "Flaky" in reason

    def test_unstable_count_too_low(self, gate, tmp_root):
        _mock_runs(gate, [30, 30, 30])
        stable, reason = gate.run(verbose=False)
        assert stable is False
        assert "too low" in reason

    def test_unstable_runner_error(self, gate, tmp_root):
        gate._run_once = lambda: (0, "pytest crashed")
        stable, reason = gate.run(verbose=False)
        assert stable is False
        assert "failed" in reason.lower()


# ── TestWriteGate ─────────────────────────────────────────────────────────────

class TestWriteGate:
    def test_creates_env_file_when_absent(self, gate, tmp_root):
        _mock_runs(gate, [100, 100, 100])
        gate.run(verbose=False)
        env_file = tmp_root / ".env"
        assert env_file.exists()
        content = env_file.read_text()
        assert "AWOS_SAFE_TO_RUN_TESTS=true" in content

    def test_writes_false_on_unstable(self, gate, tmp_root):
        _mock_runs(gate, [100, 90, 100])
        gate.run(verbose=False)
        content = (tmp_root / ".env").read_text()
        assert "AWOS_SAFE_TO_RUN_TESTS=false" in content

    def test_updates_existing_key(self, gate, tmp_root):
        env = tmp_root / ".env"
        env.write_text("DEEPSEEK_API_KEY=abc\nAWOS_SAFE_TO_RUN_TESTS=false\n")
        _mock_runs(gate, [100, 100, 100])
        gate.run(verbose=False)
        content = env.read_text()
        assert content.count("AWOS_SAFE_TO_RUN_TESTS") == 1
        assert "AWOS_SAFE_TO_RUN_TESTS=true" in content
        assert "DEEPSEEK_API_KEY=abc" in content

    def test_preserves_other_env_vars(self, gate, tmp_root):
        env = tmp_root / ".env"
        env.write_text("MY_KEY=xyz\nOTHER=123\n")
        _mock_runs(gate, [100, 100, 100])
        gate.run(verbose=False)
        content = env.read_text()
        assert "MY_KEY=xyz" in content
        assert "OTHER=123" in content

    def test_updates_os_environ(self, gate, tmp_root, monkeypatch):
        monkeypatch.delenv("AWOS_SAFE_TO_RUN_TESTS", raising=False)
        _mock_runs(gate, [100, 100, 100])
        gate.run(verbose=False)
        assert os.environ.get("AWOS_SAFE_TO_RUN_TESTS") == "true"

    def test_current_gate_reflects_env(self, gate, monkeypatch):
        monkeypatch.setenv("AWOS_SAFE_TO_RUN_TESTS", "true")
        assert gate.current_gate() is True
        monkeypatch.setenv("AWOS_SAFE_TO_RUN_TESTS", "false")
        assert gate.current_gate() is False


# ── TestParsePassCount ────────────────────────────────────────────────────────

class TestParsePassCount:
    def test_standard(self, gate):
        assert gate._parse_pass_count("747 passed, 39 skipped in 78s") == 747

    def test_no_match(self, gate):
        assert gate._parse_pass_count("ERROR: collection failed") == 0
