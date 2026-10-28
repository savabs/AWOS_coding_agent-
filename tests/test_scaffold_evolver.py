"""Tests for ScaffoldEvolver — Feature 1C (true self-learning)."""

from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

from scaffold.agent.scaffold_evolver import ScaffoldEvolver, MutationResult


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_root(tmp_path):
    """Fake scaffold root with a dummy target file."""
    scaffold = tmp_path / "scaffold" / "agent"
    scaffold.mkdir(parents=True)
    (scaffold / "self_correction.py").write_text(
        'def classify_error(msg):\n    return "UNKNOWN"\n'
    )
    (scaffold / "worker.py").write_text(
        'def execute_task(task):\n    return {"success": True}\n'
    )
    awos = tmp_path / ".awos"
    awos.mkdir()
    return tmp_path


@pytest.fixture
def cheap_call():
    return MagicMock(return_value="")


@pytest.fixture
def evolver(tmp_root, cheap_call, monkeypatch):
    monkeypatch.setenv("AWOS_SCAFFOLD_EVOLUTION", "true")
    monkeypatch.setenv("AWOS_SAFE_TO_RUN_TESTS", "true")
    e = ScaffoldEvolver(scaffold_root=str(tmp_root), cheap_call=cheap_call)
    e._mutations_log = tmp_root / ".awos" / "scaffold_mutations.jsonl"
    return e


def _make_error_store(error_type: str = "SYNTAX_ERROR", count: int = 5):
    """Create a mock ErrorPatternStore with synthetic patterns."""
    from unittest.mock import MagicMock
    pattern = MagicMock()
    pattern.error_type = error_type
    pattern.error_msg = f"example {error_type} error"
    store = MagicMock()
    store.list_all.return_value = [pattern] * count
    return store


def _valid_patch_response(search: str, replace: str) -> str:
    return f"<<<SEARCH>>>\n{search}\n<<<REPLACE>>>\n{replace}\n<<<END>>>"


# ── TestShouldEvolve ──────────────────────────────────────────────────────────

class TestShouldEvolve:
    def test_disabled_by_default(self, tmp_root, monkeypatch):
        monkeypatch.delenv("AWOS_SCAFFOLD_EVOLUTION", raising=False)
        monkeypatch.delenv("AWOS_SAFE_TO_RUN_TESTS", raising=False)
        e = ScaffoldEvolver(scaffold_root=str(tmp_root), cheap_call=MagicMock())
        assert e.should_evolve(0.6) is False

    def test_enabled_above_threshold(self, evolver):
        assert evolver.should_evolve(0.5) is True

    def test_below_threshold_false(self, evolver):
        assert evolver.should_evolve(0.2) is False

    def test_at_exact_threshold_true(self, evolver):
        assert evolver.should_evolve(0.40) is True

    def test_safe_to_run_tests_required(self, tmp_root, monkeypatch):
        monkeypatch.setenv("AWOS_SCAFFOLD_EVOLUTION", "true")
        monkeypatch.delenv("AWOS_SAFE_TO_RUN_TESTS", raising=False)
        e = ScaffoldEvolver(scaffold_root=str(tmp_root), cheap_call=MagicMock())
        assert e.should_evolve(0.9) is False

    def test_no_cheap_call_false(self, tmp_root, monkeypatch):
        monkeypatch.setenv("AWOS_SCAFFOLD_EVOLUTION", "true")
        monkeypatch.setenv("AWOS_SAFE_TO_RUN_TESTS", "true")
        e = ScaffoldEvolver(scaffold_root=str(tmp_root), cheap_call=None)
        assert e.should_evolve(0.9) is False

    def test_custom_threshold(self, tmp_root, monkeypatch):
        monkeypatch.setenv("AWOS_SCAFFOLD_EVOLUTION", "true")
        monkeypatch.setenv("AWOS_SAFE_TO_RUN_TESTS", "true")
        monkeypatch.setenv("AWOS_EVOLVE_THRESHOLD", "0.6")
        e = ScaffoldEvolver(scaffold_root=str(tmp_root), cheap_call=MagicMock())
        assert e.should_evolve(0.5) is False
        assert e.should_evolve(0.7) is True


# ── TestParsePatch ────────────────────────────────────────────────────────────

class TestParsePatch:
    def test_valid_patch(self, evolver):
        raw = _valid_patch_response("old code", "new code")
        result = evolver._parse_patch(raw)
        assert result == ("old code", "new code")

    def test_missing_markers_returns_none(self, evolver):
        assert evolver._parse_patch("no markers here") is None

    def test_empty_search_returns_none(self, evolver):
        raw = "<<<SEARCH>>>\n\n<<<REPLACE>>>\nnew code\n<<<END>>>"
        assert evolver._parse_patch(raw) is None

    def test_empty_replace_returns_none(self, evolver):
        raw = "<<<SEARCH>>>\nold code\n<<<REPLACE>>>\n\n<<<END>>>"
        assert evolver._parse_patch(raw) is None


# ── TestParsePassCount ────────────────────────────────────────────────────────

class TestParsePassCount:
    def test_standard_pytest_output(self, evolver):
        output = "42 passed, 3 skipped, 1 warning in 12.3s"
        assert evolver._parse_pass_count(output) == 42

    def test_no_match_returns_zero(self, evolver):
        assert evolver._parse_pass_count("ERROR: no tests found") == 0

    def test_large_number(self, evolver):
        output = "674 passed, 39 skipped in 84s"
        assert evolver._parse_pass_count(output) == 674


# ── TestEvolveOnce ────────────────────────────────────────────────────────────

class TestEvolveOnce:
    def test_no_error_patterns_rejected(self, evolver):
        store = MagicMock()
        store.retrieve_all.return_value = []
        result = evolver.evolve_once(store, test_cmd=["echo", "0 passed"])
        assert result.accepted is False
        assert "no" in result.rejection_reason.lower()

    def test_baseline_too_low_rejected(self, evolver):
        store = _make_error_store("SYNTAX_ERROR", 5)
        evolver._run_tests = MagicMock(return_value=10)
        result = evolver.evolve_once(store, test_cmd=["echo", ""])
        assert result.accepted is False
        assert "baseline" in result.rejection_reason

    def test_search_text_not_found_rejected(self, evolver):
        store = _make_error_store("SYNTAX_ERROR", 5)
        evolver._run_tests = MagicMock(return_value=100)
        evolver._cheap_call.return_value = _valid_patch_response(
            "THIS TEXT IS NOT IN THE FILE", "replacement"
        )
        result = evolver.evolve_once(store, test_cmd=["echo", ""])
        assert result.accepted is False
        assert "mismatch" in result.rejection_reason

    def test_accepted_when_tests_stable(self, evolver, tmp_root):
        store = _make_error_store("SYNTAX_ERROR", 5)
        evolver._run_tests = MagicMock(return_value=100)
        target = tmp_root / "scaffold" / "agent" / "self_correction.py"
        original = target.read_text()
        evolver._cheap_call.return_value = _valid_patch_response(
            'return "UNKNOWN"', 'return "UNKNOWN"  # improved'
        )
        result = evolver.evolve_once(store, test_cmd=["echo", ""])
        assert result.accepted is True
        assert target.read_text() != original

    def test_rejected_and_rolled_back_when_tests_regress(self, evolver, tmp_root):
        store = _make_error_store("SYNTAX_ERROR", 5)
        call_count = [0]

        def mock_run_tests(cmd):
            call_count[0] += 1
            return 100 if call_count[0] == 1 else 50

        evolver._run_tests = mock_run_tests
        target = tmp_root / "scaffold" / "agent" / "self_correction.py"
        original = target.read_text()
        evolver._cheap_call.return_value = _valid_patch_response(
            'return "UNKNOWN"', 'return "REGRESSED"'
        )
        result = evolver.evolve_once(store, test_cmd=["echo", ""])
        assert result.accepted is False
        assert "regress" in result.rejection_reason.lower() or "dropped" in result.rejection_reason
        assert target.read_text() == original

    def test_no_patch_proposed_rejected(self, evolver):
        store = _make_error_store("SYNTAX_ERROR", 5)
        evolver._run_tests = MagicMock(return_value=100)
        evolver._cheap_call.return_value = "no patch here"
        result = evolver.evolve_once(store, test_cmd=["echo", ""])
        assert result.accepted is False
        assert "patch" in result.rejection_reason.lower()


# ── TestLoadMutations ─────────────────────────────────────────────────────────

class TestLoadMutations:
    def test_empty_when_no_log(self, evolver):
        assert evolver.load_mutations() == []

    def test_loads_recorded_mutations(self, evolver):
        m = MutationResult(
            accepted=True, target_file="f.py", description="desc",
            error_type="SYNTAX_ERROR", tests_before=100, tests_after=102,
            timestamp="2026-01-01T00:00:00Z",
        )
        evolver._record(m)
        loaded = evolver.load_mutations()
        assert len(loaded) == 1
        assert loaded[0].accepted is True
        assert loaded[0].target_file == "f.py"
