"""
Tests for SelfCorrectionEngine (v4).

Covers:
  - ErrorClass classification from error strings
  - CorrectionHint.format_for_prompt() output
  - Anchor candidate extraction for SEARCH_NOT_FOUND
  - suggest() returns correct approach per error type
  - Orchestrator wires SelfCorrectionEngine (smoke test)
"""

import sys
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scaffold.agent.self_correction import (
    SelfCorrectionEngine,
    CorrectionHint,
    ErrorClass,
    _find_anchor_candidates,
)


# ── ErrorClass classification ─────────────────────────────────────────────────

class TestClassify:
    def setup_method(self):
        self.engine = SelfCorrectionEngine()

    def test_search_not_found(self):
        assert self.engine.classify("search text not found in file") == ErrorClass.SEARCH_NOT_FOUND

    def test_search_not_found_variant(self):
        assert self.engine.classify("Could not find exact text in context") == ErrorClass.SEARCH_NOT_FOUND

    def test_syntax_error(self):
        assert self.engine.classify("SyntaxError: invalid syntax on line 12") == ErrorClass.SYNTAX_ERROR

    def test_indentation_error(self):
        assert self.engine.classify("IndentationError: unexpected indent") == ErrorClass.SYNTAX_ERROR

    def test_format_malformed(self):
        assert self.engine.classify("missing SEARCH block in output") == ErrorClass.FORMAT_MALFORMED

    def test_format_parse_error(self):
        assert self.engine.classify("Could not parse output format") == ErrorClass.FORMAT_MALFORMED

    def test_empty_output(self):
        assert self.engine.classify("empty response from model") == ErrorClass.EMPTY_OUTPUT

    def test_no_change(self):
        assert self.engine.classify("no code change detected") == ErrorClass.EMPTY_OUTPUT

    def test_unknown_falls_through(self):
        assert self.engine.classify("something completely unexpected happened") == ErrorClass.UNKNOWN

    def test_case_insensitive(self):
        assert self.engine.classify("SYNTAXERROR: BAD CODE") == ErrorClass.SYNTAX_ERROR


# ── CorrectionHint format ─────────────────────────────────────────────────────

class TestCorrectionHintFormat:
    def test_format_contains_approach_name(self):
        hint = CorrectionHint(
            error_class=ErrorClass.SYNTAX_ERROR,
            approach_name="syntax_repair",
            hint="Fix only the broken line.",
            focus_hint="Check indentation.",
        )
        text = hint.format_for_prompt()
        assert "syntax_repair" in text
        assert "Fix only the broken line." in text
        assert "Check indentation." in text

    def test_format_starts_with_correction_header(self):
        hint = CorrectionHint(
            error_class=ErrorClass.UNKNOWN,
            approach_name="fresh_approach",
            hint="Try again.",
            focus_hint="Target a single line.",
        )
        assert hint.format_for_prompt().startswith("[SELF-CORRECTION")

    def test_format_is_string(self):
        hint = CorrectionHint(
            error_class=ErrorClass.EMPTY_OUTPUT,
            approach_name="minimal_change",
            hint="Make one small change.",
            focus_hint="Target function body.",
        )
        assert isinstance(hint.format_for_prompt(), str)


# ── Anchor candidate extraction ───────────────────────────────────────────────

class TestAnchorCandidates:
    _FILE = """
class AuthManager:
    def validate_token(self, token):
        pass

    def refresh_token(self, token):
        pass

def setup_auth(config):
    pass

class Database:
    def connect(self):
        pass
"""

    def test_finds_relevant_defs(self):
        candidates = _find_anchor_candidates("validate token expiry", self._FILE)
        assert any("validate_token" in c for c in candidates)

    def test_ignores_irrelevant_defs(self):
        candidates = _find_anchor_candidates("validate token", self._FILE)
        assert not any("connect" in c for c in candidates)

    def test_returns_empty_for_no_match(self):
        candidates = _find_anchor_candidates("unrelated_xyz_action", self._FILE)
        assert candidates == []

    def test_returns_list(self):
        result = _find_anchor_candidates("setup auth", self._FILE)
        assert isinstance(result, list)


# ── suggest() integration ─────────────────────────────────────────────────────

class TestSuggest:
    def setup_method(self):
        self.engine = SelfCorrectionEngine()
        self.task = {
            "task_id": 1,
            "action": "fix validate token function",
            "file": "auth.py",
            "complexity": "medium",
        }
        self.file_content = """
def validate_token(token):
    return token is not None

def refresh_token(token):
    return token + "_new"
"""

    def test_returns_correction_hint(self):
        hint = self.engine.suggest(self.task, "search text not found", self.file_content, 2)
        assert isinstance(hint, CorrectionHint)

    def test_search_not_found_enriches_focus_with_anchor(self):
        hint = self.engine.suggest(
            self.task, "search text not found in file", self.file_content, 2
        )
        assert hint.error_class == ErrorClass.SEARCH_NOT_FOUND
        assert "validate_token" in hint.focus_hint or "Candidate anchor" in hint.focus_hint

    def test_syntax_error_gives_syntax_repair(self):
        hint = self.engine.suggest(self.task, "SyntaxError: invalid syntax", self.file_content, 2)
        assert hint.approach_name == "syntax_repair"

    def test_unknown_error_gives_fresh_approach(self):
        hint = self.engine.suggest(self.task, "something weird happened", self.file_content, 2)
        assert hint.approach_name == "fresh_approach"

    def test_format_for_prompt_is_non_empty(self):
        hint = self.engine.suggest(self.task, "missing REPLACE block", self.file_content, 2)
        text = hint.format_for_prompt()
        assert len(text) > 30

    def test_all_error_types_return_hint(self):
        errors = [
            "search text not found",
            "SyntaxError: invalid syntax",
            "missing SEARCH block",
            "empty response",
            "totally unknown failure",
        ]
        for err in errors:
            hint = self.engine.suggest(self.task, err, self.file_content, 2)
            assert isinstance(hint, CorrectionHint)
            assert hint.approach_name


# ── Orchestrator smoke test ───────────────────────────────────────────────────

@pytest.mark.integration
class TestOrchestratorHasSelfCorrection:
    def test_orchestrator_initializes_self_correction(self, monkeypatch):
        from scaffold.agent import planner as _planner_mod
        from scaffold.agent import worker as _worker_mod
        from scaffold.agent.self_correction import SelfCorrectionEngine
        monkeypatch.setattr(_planner_mod.Planner, "__init__", lambda self, *a, **kw: None)
        monkeypatch.setattr(_worker_mod.Worker, "__init__", lambda self, *a, **kw: None)
        from scaffold.agent.orchestrator import Orchestrator
        orch = Orchestrator()
        assert hasattr(orch, "self_correction")
        assert isinstance(orch.self_correction, SelfCorrectionEngine)
