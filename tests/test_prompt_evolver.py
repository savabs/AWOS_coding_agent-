"""Tests for PromptEvolver — Feature 1A (true self-learning)."""

from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from scaffold.agent.prompt_evolver import PromptEvolver


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_store(tmp_path):
    store = tmp_path / ".awos"
    store.mkdir()
    return store


@pytest.fixture
def evolver(tmp_store):
    cheap = MagicMock(return_value="{}")
    return PromptEvolver(store_path=str(tmp_store), cheap_call=cheap)


def _make_patterns(error_type: str, n: int = 3) -> list:
    return [{"error_type": error_type, "critique": f"example {i}", "error_msg": "msg"} for i in range(n)]


def _make_skills(task_type: str = "bug_fix", win_rate: float = 0.9) -> list:
    return [{"task_type": task_type, "keywords": ["fix", "error"], "model_used": "deepseek", "win_rate": win_rate}]


# ── TestShouldEvolve ─────────────────────────────────────────────────────────

class TestShouldEvolve:
    def test_disabled_by_default(self, evolver, monkeypatch):
        monkeypatch.delenv("AWOS_PROMPT_EVOLUTION", raising=False)
        assert evolver.should_evolve(10) is False

    def test_enabled_at_boundary(self, evolver, monkeypatch):
        monkeypatch.setenv("AWOS_PROMPT_EVOLUTION", "true")
        assert evolver.should_evolve(10) is True

    def test_not_between_boundaries(self, evolver, monkeypatch):
        monkeypatch.setenv("AWOS_PROMPT_EVOLUTION", "true")
        assert evolver.should_evolve(7) is False

    def test_session_zero_never_triggers(self, evolver, monkeypatch):
        monkeypatch.setenv("AWOS_PROMPT_EVOLUTION", "true")
        assert evolver.should_evolve(0) is False

    def test_no_cheap_call_returns_false(self, tmp_store, monkeypatch):
        monkeypatch.setenv("AWOS_PROMPT_EVOLUTION", "true")
        e = PromptEvolver(store_path=str(tmp_store), cheap_call=None)
        assert e.should_evolve(10) is False

    def test_custom_interval(self, tmp_store, monkeypatch):
        monkeypatch.setenv("AWOS_PROMPT_EVOLUTION", "true")
        monkeypatch.setenv("AWOS_PROMPT_EVOLVE_EVERY", "5")
        e = PromptEvolver(store_path=str(tmp_store), cheap_call=MagicMock(return_value="{}"))
        assert e.should_evolve(5) is True
        assert e.should_evolve(10) is True
        assert e.should_evolve(6) is False


# ── TestEvolve ───────────────────────────────────────────────────────────────

class TestEvolve:
    def test_no_evidence_returns_empty(self, evolver):
        result = evolver.evolve(error_patterns_raw=[], skill_entries_raw=[])
        assert result == ""

    def test_empty_json_returns_empty(self, evolver):
        evolver._cheap_call.return_value = "{}"
        result = evolver.evolve(error_patterns_raw=_make_patterns("SYNTAX_ERROR"))
        assert result == ""

    def test_valid_guideline_returned(self, evolver):
        guideline_json = json.dumps({
            "guidelines": [{
                "section": "syntax",
                "guideline": "Always verify syntax before replacing code.",
                "reason": "SYNTAX_ERROR occurred 5 times",
                "confidence": 0.85,
            }]
        })
        evolver._cheap_call.return_value = guideline_json
        result = evolver.evolve(error_patterns_raw=_make_patterns("SYNTAX_ERROR", 5))
        assert "Always verify syntax" in result

    def test_low_confidence_dropped(self, evolver):
        guideline_json = json.dumps({
            "guidelines": [{
                "section": "task",
                "guideline": "Do something different.",
                "reason": "just a guess",
                "confidence": 0.3,
            }]
        })
        evolver._cheap_call.return_value = guideline_json
        result = evolver.evolve(error_patterns_raw=_make_patterns("X"))
        assert result == ""

    def test_multiple_guidelines_joined(self, evolver):
        guideline_json = json.dumps({
            "guidelines": [
                {"section": "s1", "guideline": "Check indentation.", "reason": "r1", "confidence": 0.8},
                {"section": "s2", "guideline": "Verify imports exist.", "reason": "r2", "confidence": 0.9},
            ]
        })
        evolver._cheap_call.return_value = guideline_json
        result = evolver.evolve(error_patterns_raw=_make_patterns("SYNTAX_ERROR", 5))
        assert "Check indentation" in result
        assert "Verify imports" in result

    def test_invalid_json_returns_empty(self, evolver):
        evolver._cheap_call.return_value = "NOT JSON AT ALL"
        result = evolver.evolve(error_patterns_raw=_make_patterns("X"))
        assert result == ""

    def test_markdown_fenced_json_parsed(self, evolver):
        guideline_json = json.dumps({
            "guidelines": [{
                "section": "s",
                "guideline": "Always include context lines.",
                "reason": "SEARCH_NOT_FOUND frequent",
                "confidence": 0.8,
            }]
        })
        evolver._cheap_call.return_value = f"```json\n{guideline_json}\n```"
        result = evolver.evolve(error_patterns_raw=_make_patterns("SEARCH_NOT_FOUND", 4))
        assert "Always include context lines" in result

    def test_llm_exception_returns_empty(self, evolver):
        evolver._cheap_call.side_effect = RuntimeError("API down")
        result = evolver.evolve(error_patterns_raw=_make_patterns("X"))
        assert result == ""

    def test_no_cheap_call_returns_empty(self, tmp_store):
        e = PromptEvolver(store_path=str(tmp_store), cheap_call=None)
        result = e.evolve(error_patterns_raw=_make_patterns("X"))
        assert result == ""


# ── TestPersistAndLoad ────────────────────────────────────────────────────────

class TestPersistAndLoad:
    def test_persist_creates_file(self, evolver, tmp_store):
        evolver.persist("- Be precise.", session_count=10)
        assert (tmp_store / "evolved_prompt.json").exists()

    def test_persist_content(self, evolver, tmp_store):
        evolver.persist("- Check syntax first.", session_count=20)
        data = json.loads((tmp_store / "evolved_prompt.json").read_text())
        assert data["evolved_guidelines"] == "- Check syntax first."
        assert data["session_count"] == 20
        assert data["changes_applied"] == 1

    def test_load_returns_guidelines_when_present(self, evolver, tmp_store):
        evolver.persist("- my guideline", session_count=10)
        result = evolver.load_evolved_guidelines()
        assert result == "- my guideline"

    def test_load_returns_empty_when_absent(self, evolver):
        result = evolver.load_evolved_guidelines()
        assert result == ""

    def test_load_returns_empty_on_corrupt_file(self, evolver, tmp_store):
        (tmp_store / "evolved_prompt.json").write_text("NOT JSON")
        assert evolver.load_evolved_guidelines() == ""

    def test_load_returns_empty_on_blank_guidelines(self, evolver, tmp_store):
        (tmp_store / "evolved_prompt.json").write_text(json.dumps({"evolved_guidelines": "  "}))
        assert evolver.load_evolved_guidelines() == ""


# ── TestReadHelpers ───────────────────────────────────────────────────────────

class TestReadHelpers:
    def test_read_error_patterns(self, evolver, tmp_store):
        lines = [
            json.dumps({"error_type": "SYNTAX_ERROR", "critique": "bad indent"}),
            json.dumps({"error_type": "SEARCH_NOT_FOUND", "critique": "wrong ctx"}),
        ]
        (tmp_store / "error_patterns.jsonl").write_text("\n".join(lines))
        patterns = evolver._read_error_patterns()
        assert len(patterns) == 2
        assert patterns[0]["error_type"] == "SYNTAX_ERROR"

    def test_read_error_patterns_missing_file(self, evolver):
        assert evolver._read_error_patterns() == []

    def test_read_skill_entries_list(self, evolver, tmp_store):
        skills_dir = tmp_store / "skills"
        skills_dir.mkdir()
        entries = [{"task_type": "bug_fix", "win_rate": 0.9, "model_used": "deepseek", "keywords": []}]
        (skills_dir / "index.json").write_text(json.dumps(entries))
        result = evolver._read_skill_entries()
        assert len(result) == 1
        assert result[0]["task_type"] == "bug_fix"

    def test_read_skill_entries_missing_file(self, evolver):
        assert evolver._read_skill_entries() == []

    def test_summarise_failures_counts(self, evolver):
        patterns = _make_patterns("SYNTAX_ERROR", 3) + _make_patterns("SEARCH_NOT_FOUND", 1)
        summary = evolver._summarise_failures(patterns)
        assert "SYNTAX_ERROR" in summary
        assert "3 times" in summary

    def test_summarise_successes(self, evolver):
        skills = _make_skills("bug_fix", 0.9)
        summary = evolver._summarise_successes(skills)
        assert "bug_fix" in summary
        assert "90%" in summary
