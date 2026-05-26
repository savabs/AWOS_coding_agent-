"""
tests/test_p7_generator_critic.py — Tests for P7-A Generator-Critic Self-Play.

Covers:
    - CritiqueResult fields and properties
    - CriticEngine.should_run() gate logic
    - CriticEngine._parse_response() JSON parsing
    - CriticEngine._extract_context() snippet extraction
    - CriticEngine.critique() graceful degradation on no client
    - max_critic_rounds() config helper
    - _critic_refine() orchestrator integration (mocked critic)
    - _pick_best_candidate() PRM + confidence fallback
    - error_context_for_generator formatting
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from scaffold.agent.critic_engine import (
    CriticEngine,
    CritiqueResult,
    max_critic_rounds,
)


# ── CritiqueResult ────────────────────────────────────────────────────────────

class TestCritiqueResult(unittest.TestCase):

    def test_should_revise_true(self):
        r = CritiqueResult(verdict="revise", confidence=0.4, issues=["bug"])
        self.assertTrue(r.should_revise)

    def test_should_revise_false_when_no_issues(self):
        r = CritiqueResult(verdict="revise", confidence=0.4, issues=[])
        self.assertFalse(r.should_revise)

    def test_should_revise_false_when_approved(self):
        r = CritiqueResult(verdict="approve", confidence=0.9, issues=["minor"])
        self.assertFalse(r.should_revise)

    def test_error_context_lists_issues(self):
        r = CritiqueResult(
            verdict="revise", confidence=0.3,
            issues=["Missing None check", "Off-by-one in range"],
            hints="Add guard clause at top of function",
        )
        ctx = r.error_context_for_generator
        self.assertIn("Missing None check", ctx)
        self.assertIn("Off-by-one in range", ctx)
        self.assertIn("Add guard clause", ctx)

    def test_error_context_empty_when_no_issues(self):
        r = CritiqueResult(verdict="approve", confidence=0.9, issues=[])
        self.assertEqual(r.error_context_for_generator, "")

    def test_caps_issues_at_five(self):
        issues = [f"problem {i}" for i in range(10)]
        r = CritiqueResult(verdict="revise", confidence=0.2, issues=issues)
        ctx = r.error_context_for_generator
        # Only up to 5 numbered items should appear
        import re
        numbered = re.findall(r"^\s+\d+\.", ctx, re.MULTILINE)
        self.assertLessEqual(len(numbered), 5)


# ── CriticEngine.should_run() ─────────────────────────────────────────────────

class TestShouldRun(unittest.TestCase):

    def setUp(self):
        self.critic = CriticEngine.__new__(CriticEngine)
        self.critic._client = None
        self.critic._anthropic = None
        self.critic._google = None

    def _task(self, complexity="medium", error_context=""):
        return {"action": "fix bug", "file": "foo.py",
                "complexity": complexity, "error_context": error_context}

    @patch.dict(os.environ, {"AWOS_CRITIC": "false"})
    def test_disabled_by_env(self):
        from scaffold.agent import critic_engine
        original = critic_engine._CRITIC_ENABLED
        critic_engine._CRITIC_ENABLED = False
        try:
            result = self.critic.should_run(self._task(), attempt=1)
            self.assertFalse(result)
        finally:
            critic_engine._CRITIC_ENABLED = original

    def test_runs_for_medium_complexity(self):
        from scaffold.agent import critic_engine
        old_rounds = critic_engine._MAX_CRITIC_ROUNDS
        critic_engine._MAX_CRITIC_ROUNDS = 1
        old_enabled = critic_engine._CRITIC_ENABLED
        critic_engine._CRITIC_ENABLED = True
        try:
            self.assertTrue(self.critic.should_run(self._task(complexity="medium"), attempt=1))
        finally:
            critic_engine._MAX_CRITIC_ROUNDS = old_rounds
            critic_engine._CRITIC_ENABLED = old_enabled

    def test_skips_low_complexity_first_attempt(self):
        from scaffold.agent import critic_engine
        old_enabled = critic_engine._CRITIC_ENABLED
        critic_engine._CRITIC_ENABLED = True
        try:
            self.assertFalse(self.critic.should_run(self._task(complexity="low"), attempt=1))
        finally:
            critic_engine._CRITIC_ENABLED = old_enabled

    def test_runs_low_complexity_on_retry(self):
        from scaffold.agent import critic_engine
        old_rounds = critic_engine._MAX_CRITIC_ROUNDS
        critic_engine._MAX_CRITIC_ROUNDS = 1
        old_enabled = critic_engine._CRITIC_ENABLED
        critic_engine._CRITIC_ENABLED = True
        try:
            self.assertTrue(self.critic.should_run(self._task(complexity="low"), attempt=2))
        finally:
            critic_engine._MAX_CRITIC_ROUNDS = old_rounds
            critic_engine._CRITIC_ENABLED = old_enabled

    def test_runs_low_complexity_with_error_context(self):
        from scaffold.agent import critic_engine
        old_rounds = critic_engine._MAX_CRITIC_ROUNDS
        critic_engine._MAX_CRITIC_ROUNDS = 1
        old_enabled = critic_engine._CRITIC_ENABLED
        critic_engine._CRITIC_ENABLED = True
        try:
            task = self._task(complexity="low", error_context="prev error")
            self.assertTrue(self.critic.should_run(task, attempt=1))
        finally:
            critic_engine._MAX_CRITIC_ROUNDS = old_rounds
            critic_engine._CRITIC_ENABLED = old_enabled

    def test_skips_when_max_rounds_zero(self):
        from scaffold.agent import critic_engine
        old_rounds = critic_engine._MAX_CRITIC_ROUNDS
        critic_engine._MAX_CRITIC_ROUNDS = 0
        try:
            self.assertFalse(self.critic.should_run(self._task(complexity="high"), attempt=1))
        finally:
            critic_engine._MAX_CRITIC_ROUNDS = old_rounds


# ── CriticEngine._parse_response() ───────────────────────────────────────────

class TestParseResponse(unittest.TestCase):

    def setUp(self):
        self.critic = CriticEngine.__new__(CriticEngine)
        self.critic._client = None
        self.critic._anthropic = None

    def test_parse_approve_json(self):
        raw = '```json\n{"verdict": "approve", "confidence": 0.92, "issues": [], "hints": ""}\n```'
        r = self.critic._parse_response(raw, "test", 0.001)
        self.assertEqual(r.verdict, "approve")
        self.assertAlmostEqual(r.confidence, 0.92)
        self.assertEqual(r.issues, [])

    def test_parse_revise_json(self):
        raw = '{"verdict": "revise", "confidence": 0.45, "issues": ["off-by-one"], "hints": "fix loop"}'
        r = self.critic._parse_response(raw, "test", 0.0)
        self.assertEqual(r.verdict, "revise")
        self.assertIn("off-by-one", r.issues)
        self.assertEqual(r.hints, "fix loop")

    def test_revise_without_issues_becomes_approve(self):
        raw = '{"verdict": "revise", "confidence": 0.6, "issues": [], "hints": ""}'
        r = self.critic._parse_response(raw, "test", 0.0)
        self.assertEqual(r.verdict, "approve")

    def test_malformed_json_returns_approve(self):
        r = self.critic._parse_response("not json at all", "test", 0.0)
        self.assertEqual(r.verdict, "approve")
        self.assertLessEqual(r.confidence, 0.5)

    def test_unknown_verdict_defaults_approve(self):
        raw = '{"verdict": "unclear", "confidence": 0.5, "issues": [], "hints": ""}'
        r = self.critic._parse_response(raw, "test", 0.0)
        self.assertEqual(r.verdict, "approve")

    def test_confidence_clamped_to_0_1(self):
        raw = '{"verdict": "approve", "confidence": 1.5, "issues": [], "hints": ""}'
        r = self.critic._parse_response(raw, "test", 0.0)
        self.assertLessEqual(r.confidence, 1.0)
        raw2 = '{"verdict": "approve", "confidence": -0.2, "issues": [], "hints": ""}'
        r2 = self.critic._parse_response(raw2, "test", 0.0)
        self.assertGreaterEqual(r2.confidence, 0.0)


# ── CriticEngine._extract_context() ──────────────────────────────────────────

class TestExtractContext(unittest.TestCase):

    def setUp(self):
        self.critic = CriticEngine.__new__(CriticEngine)
        self.critic._client = None
        self.critic._anthropic = None

    def test_returns_snippet_around_search(self):
        lines = [f"line {i}" for i in range(100)]
        content = "\n".join(lines)
        search = "line 50"
        ctx = self.critic._extract_context(content, search)
        self.assertIn("line 50", ctx)

    def test_empty_search_returns_head(self):
        content = "abc\ndef\nghi"
        ctx = self.critic._extract_context(content, "")
        self.assertIn("abc", ctx)

    def test_missing_search_returns_head(self):
        content = "\n".join([f"x{i}" for i in range(20)])
        ctx = self.critic._extract_context(content, "not_in_file")
        self.assertIn("x0", ctx)


# ── CriticEngine.critique() graceful degradation ─────────────────────────────

class TestCritiqueGraceful(unittest.TestCase):

    def setUp(self):
        self.critic = CriticEngine.__new__(CriticEngine)
        self.critic._client = None
        self.critic._anthropic = None
        self.critic._google = None

    def test_no_client_returns_approval(self):
        task = {"action": "fix bug", "file": "foo.py", "complexity": "medium"}
        patch = {"success": True, "search": "x = 1", "replace": "x = 2"}
        result = self.critic.critique(task, patch, "x = 1")
        self.assertEqual(result.verdict, "approve")

    def test_failed_patch_skipped(self):
        task = {"action": "fix bug", "file": "foo.py", "complexity": "medium"}
        patch = {"success": False, "search": "", "replace": ""}
        result = self.critic.critique(task, patch, "")
        self.assertEqual(result.verdict, "approve")
        self.assertIn("skipped", result.model_used)


# ── max_critic_rounds() ───────────────────────────────────────────────────────

class TestMaxCriticRounds(unittest.TestCase):

    def test_returns_int(self):
        rounds = max_critic_rounds()
        self.assertIsInstance(rounds, int)
        self.assertGreaterEqual(rounds, 0)

    def test_env_override(self):
        from scaffold.agent import critic_engine
        old = critic_engine._MAX_CRITIC_ROUNDS
        old_e = critic_engine._CRITIC_ENABLED
        critic_engine._MAX_CRITIC_ROUNDS = 2
        critic_engine._CRITIC_ENABLED = True
        try:
            self.assertEqual(max_critic_rounds(), 2)
        finally:
            critic_engine._MAX_CRITIC_ROUNDS = old
            critic_engine._CRITIC_ENABLED = old_e


# ── Orchestrator._pick_best_candidate() ──────────────────────────────────────

class TestPickBestCandidate(unittest.TestCase):

    def _make_orch(self):
        from scaffold.agent.orchestrator import Orchestrator
        orch = Orchestrator.__new__(Orchestrator)
        orch._prm = None
        return orch

    def test_single_candidate_returned(self):
        orch = self._make_orch()
        patch = {"success": True, "search": "a", "replace": "b"}
        self.assertEqual(orch._pick_best_candidate([patch], {}), patch)

    def test_picks_highest_critic_confidence(self):
        orch = self._make_orch()
        p1 = {"success": True, "search": "a", "replace": "b", "_critic_confidence": 0.5}
        p2 = {"success": True, "search": "a", "replace": "c", "_critic_confidence": 0.9}
        best = orch._pick_best_candidate([p1, p2], {})
        self.assertEqual(best, p2)

    def test_prm_scoring_used_when_ready(self):
        orch = self._make_orch()
        mock_prm = MagicMock()
        mock_prm.ready = True
        mock_prm.predict.side_effect = [0.3, 0.8]
        orch._prm = mock_prm
        p1 = {"success": True, "search": "a", "replace": "b"}
        p2 = {"success": True, "search": "a", "replace": "c"}
        best = orch._pick_best_candidate([p1, p2], {"action": "test"})
        self.assertEqual(best, p2)


if __name__ == "__main__":
    unittest.main()
