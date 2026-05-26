"""
tests/test_p2_improvements.py — Tests for P2 SOTA improvements.

Covers:
    P2.1  — Planner singleton + _classify_goal_complexity
    P2.1.5 — Worker token budget helpers + three-block context
    P2.2  — PatchCandidate scoring, normalize_patch, majority vote
    P2.3  — WorktreeManager API (no real git, mocked subprocess)
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Make scaffold importable without install
sys.path.insert(0, str(Path(__file__).parent.parent))

from scaffold.agent.planner import Planner, get_planner, _planner_instance
from scaffold.agent.worker import Worker, _PatchCandidate
from scaffold.agent.worktree import WorktreeManager


# ── P2.1 Planner Singleton + Complexity Classifier ───────────────────────────

class TestPlannerSingleton(unittest.TestCase):

    def test_classify_simple_goal(self):
        self.assertTrue(Planner._classify_goal_complexity("add method to class", {}))

    def test_classify_fix_bug(self):
        self.assertTrue(Planner._classify_goal_complexity("fix bug in login handler", {}))

    def test_classify_complex_goal(self):
        self.assertFalse(Planner._classify_goal_complexity("refactor authentication system", {}))

    def test_classify_architecture_goal(self):
        self.assertFalse(Planner._classify_goal_complexity("redesign multi-file architecture", {}))

    def test_classify_many_files_is_complex(self):
        ctx = {"files": ["a.py", "b.py", "c.py", "d.py"]}
        self.assertFalse(Planner._classify_goal_complexity("short goal", ctx))

    def test_classify_long_goal_is_complex(self):
        long_goal = "add a brand new feature that does a lot of things " * 2
        self.assertFalse(Planner._classify_goal_complexity(long_goal, {}))


# ── P2.1.5 Worker Token Budget Helpers ───────────────────────────────────────

class TestWorkerTokenBudget(unittest.TestCase):

    def setUp(self):
        self.w = Worker.__new__(Worker)
        self.w.client = None
        self.w.anthropic_client = None

    def test_estimate_tokens_empty(self):
        self.assertEqual(self.w._estimate_tokens(""), 0)

    def test_estimate_tokens_word_count(self):
        text = "hello world foo bar"  # 4 words → int(4 * 1.3) = 5
        self.assertEqual(self.w._estimate_tokens(text), 5)

    def test_truncate_already_short(self):
        text = "short text"
        result = self.w._truncate_to_tokens(text, 1000)
        self.assertEqual(result, text)

    def test_truncate_applies_limit(self):
        text = " ".join(["word"] * 200)
        result = self.w._truncate_to_tokens(text, 50)
        self.assertIn("[truncated", result)
        self.assertLess(len(result.split()), 200)

    def test_build_file_and_rag_context_no_index(self):
        task = {"file": "worker.py", "action": "add method"}
        content = "def foo(): pass"
        result = self.w._build_file_and_rag_context(content, task, codebase_index=None)
        self.assertIn("worker.py", result)
        self.assertIn("foo", result)

    def test_build_file_and_rag_context_with_index(self):
        mock_index = MagicMock()
        mock_index.query_code.return_value = "def bar(): return 42"
        task = {"file": "worker.py", "action": "add method"}
        content = "def foo(): pass"
        result = self.w._build_file_and_rag_context(content, task, codebase_index=mock_index)
        self.assertIn("bar", result)

    def test_build_file_large_content_truncated(self):
        task = {"file": "big.py", "action": "something"}
        content = "x = 1\n" * 3000  # ~6k tokens
        result = self.w._build_file_and_rag_context(content, task, codebase_index=None)
        self.assertLessEqual(self.w._estimate_tokens(result), self.w.MAX_CONTEXT_TOKENS_BLOCK_B + 50)


# ── P2.2 Patch Candidate + Majority Vote ─────────────────────────────────────

class TestPatchCandidateScoring(unittest.TestCase):

    def _make_worker(self):
        w = Worker.__new__(Worker)
        w.client = None
        w.anthropic_client = None
        return w

    def test_score_candidate_syntax_ok(self):
        w = self._make_worker()
        c = _PatchCandidate(edits=[], new_content="x=1", reasoning="", temperature=0.2, syntax_ok=True)
        score = w._score_candidate(c)
        self.assertGreaterEqual(score, 0.2)

    def test_score_candidate_syntax_fail(self):
        w = self._make_worker()
        c = _PatchCandidate(edits=[], new_content="", reasoning="", temperature=0.2, syntax_ok=False)
        score = w._score_candidate(c)
        self.assertEqual(score, 0.0)

    def test_score_with_test_result(self):
        w = self._make_worker()
        c = _PatchCandidate(edits=[], new_content="x=1", reasoning="", temperature=0.2,
                            syntax_ok=True, test_result=1.0)
        score = w._score_candidate(c)
        self.assertAlmostEqual(score, 0.2 + 0.1 + 0.7, places=3)

    def test_normalize_patch_same_content(self):
        w = self._make_worker()
        code = "def foo():\n    return 1\n"
        norm = w._normalize_patch(code, code)
        self.assertEqual(norm, "")  # no diff

    def test_majority_vote_single_candidate(self):
        w = self._make_worker()
        c = _PatchCandidate(edits=[], new_content="x=1", reasoning="", temperature=0.2,
                            syntax_ok=True, score=0.3)
        result = w._select_by_majority_vote([c], "")
        self.assertIs(result, c)

    def test_majority_vote_prefers_passing(self):
        w = self._make_worker()
        c1 = _PatchCandidate(edits=[], new_content="x=1", reasoning="", temperature=0.2,
                             syntax_ok=True, test_result=1.0, score=1.0)
        c2 = _PatchCandidate(edits=[], new_content="y=2", reasoning="", temperature=0.7,
                             syntax_ok=True, test_result=0.0, score=0.3)
        result = w._select_by_majority_vote([c1, c2], "original")
        self.assertIs(result, c1)

    def test_execute_with_sampling_gate_off(self):
        w = self._make_worker()
        w.execute_task = MagicMock(return_value={"success": True, "method": "single"})
        os.environ.pop("AWOS_PARALLEL_SAMPLING", None)
        result = w.execute_with_sampling(
            {"action": "add method", "complexity": "high"},
            "def foo(): pass",
        )
        w.execute_task.assert_called_once()

    def test_execute_with_sampling_low_complexity_skips(self):
        w = self._make_worker()
        w.execute_task = MagicMock(return_value={"success": True})
        os.environ["AWOS_PARALLEL_SAMPLING"] = "true"
        result = w.execute_with_sampling(
            {"action": "rename var", "complexity": "low"},
            "x = 1",
        )
        w.execute_task.assert_called_once()
        del os.environ["AWOS_PARALLEL_SAMPLING"]


# ── P2.3 WorktreeManager API ──────────────────────────────────────────────────

class TestWorktreeManager(unittest.TestCase):

    def test_branch_name_sanitizes(self):
        name = WorktreeManager._branch_name("my feature/branch 123xyz")
        self.assertTrue(name.startswith("awos/feature/"))
        self.assertNotIn(" ", name)

    def test_feature_id_from_goal_deterministic(self):
        id1 = WorktreeManager.feature_id_from_goal("add login")
        id2 = WorktreeManager.feature_id_from_goal("add login")
        self.assertEqual(id1, id2)

    def test_feature_id_from_goal_different_goals(self):
        id1 = WorktreeManager.feature_id_from_goal("add login")
        id2 = WorktreeManager.feature_id_from_goal("remove logout")
        self.assertNotEqual(id1, id2)

    def test_require_worktree_raises_for_unknown(self):
        wm = WorktreeManager(repo_root="/tmp")
        with self.assertRaises(KeyError):
            wm._require_worktree("nonexistent")

    @patch("subprocess.run")
    def test_create_worktree_calls_git(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            wm = WorktreeManager(repo_root=tmpdir)
            path = wm.create_worktree("abc123")
            self.assertIn("abc123", path)
            self.assertIn("abc123", wm.worktrees)

    @patch("subprocess.run")
    def test_cleanup_worktree_removes_from_dict(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        wm = WorktreeManager(repo_root="/tmp")
        wm.worktrees["feat"] = "/tmp/.awos/worktrees/feat"
        wm.cleanup_worktree("feat")
        self.assertNotIn("feat", wm.worktrees)


if __name__ == "__main__":
    unittest.main()
