"""
tests/test_p8_reflexion.py — P8 Reflexion: Learning from Failure.

Covers:
    - FailureType enum values
    - PostMortemResult fields and properties
    - PostMortemEngine.reflect() — stores critique, returns result
    - PostMortemEngine.reflect() — deduplication suppresses near-identical errors
    - PostMortemEngine.recall() — exact match, file-level, semantic fallback
    - PostMortemEngine.format_for_prompt() — formats patterns for injection
    - PostMortemEngine.stats() — summary counts
    - PostMortemEngine._semantic_recall() — keyword overlap scoring
    - PostMortemEngine graceful degradation with no LLM client
    - LearningInspector._reflexion_stats() — reads ErrorPatternStore
    - LearningReport.reflexion field populated by inspector
    - LearningReport.render() includes Reflexion section
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from scaffold.agent.post_mortem import FailureType, PostMortemEngine, PostMortemResult
from scaffold.agent.error_pattern_store import ErrorPatternStore, make_error_pattern


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_store(tmp_dir: str) -> ErrorPatternStore:
    return ErrorPatternStore(store_path=str(Path(tmp_dir) / "error_patterns.jsonl"))


def _make_engine(store, caller=None) -> PostMortemEngine:
    return PostMortemEngine(error_store=store, model_caller=caller)


def _task(action="fix null dereference in user auth", file="api/auth.py") -> dict:
    return {"action": action, "file": file, "task_id": "t1", "complexity": "medium"}


# ── FailureType ───────────────────────────────────────────────────────────────

class TestFailureType(unittest.TestCase):

    def test_all_three_types_exist(self):
        self.assertEqual(FailureType.WORKER_FAIL.value, "WORKER_FAIL")
        self.assertEqual(FailureType.VERIFY_FAIL.value, "VERIFY_FAIL")
        self.assertEqual(FailureType.TEST_FAIL.value, "TEST_FAIL")


# ── PostMortemResult ──────────────────────────────────────────────────────────

class TestPostMortemResult(unittest.TestCase):

    def test_stored_true_when_saved(self):
        r = PostMortemResult(
            critique="The patch failed because...",
            failure_type=FailureType.TEST_FAIL,
            stored=True,
            recalled=[],
        )
        self.assertTrue(r.stored)
        self.assertEqual(r.failure_type, FailureType.TEST_FAIL)

    def test_recalled_list_populated(self):
        mock_pattern = MagicMock()
        r = PostMortemResult(
            critique="",
            failure_type=FailureType.VERIFY_FAIL,
            stored=False,
            recalled=[mock_pattern],
        )
        self.assertEqual(len(r.recalled), 1)


# ── PostMortemEngine.reflect() ────────────────────────────────────────────────

class TestReflect(unittest.TestCase):

    def test_stores_critique_when_caller_returns_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            caller = lambda _p: "The agent forgot to handle the None case in line 42."
            engine = _make_engine(store, caller)
            task = _task()

            result = engine.reflect(task, "NullPointerException at line 42", FailureType.WORKER_FAIL)

            self.assertTrue(result.stored)
            self.assertIn("None", result.critique)
            patterns = store.retrieve("api/auth.py", "WORKER_FAIL", top_n=5)
            self.assertEqual(len(patterns), 1)

    def test_reflect_without_caller_does_not_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            engine = _make_engine(store, caller=None)
            result = engine.reflect(_task(), "some error", FailureType.VERIFY_FAIL)
            self.assertFalse(result.stored)
            self.assertEqual(result.critique, "")

    def test_reflect_on_test_failure_uses_correct_error_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            caller = lambda _p: "The patch broke the existing interface contract."
            engine = _make_engine(store, caller)
            engine.reflect(_task(), "test_login FAILED", FailureType.TEST_FAIL)
            patterns = store.retrieve("api/auth.py", "TEST_FAIL", top_n=3)
            self.assertEqual(len(patterns), 1)

    def test_reflect_returns_recalled_patterns(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            # Pre-seed a critique for this file
            store.save(make_error_pattern(
                task_id="t0", file_path="api/auth.py",
                error_type="WORKER_FAIL", error_msg="old error",
                critique="Previously: agent used wrong indent",
            ))
            caller = lambda _p: "New critique here."
            engine = _make_engine(store, caller)
            result = engine.reflect(_task(), "new error", FailureType.WORKER_FAIL)
            self.assertGreater(len(result.recalled), 0)

    def test_deduplication_suppresses_identical_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            caller = MagicMock(return_value="critique text")
            engine = _make_engine(store, caller)
            error = "AttributeError: 'NoneType' object has no attribute 'get'"

            engine.reflect(_task(), error, FailureType.WORKER_FAIL)
            engine.reflect(_task(), error, FailureType.WORKER_FAIL)

            # caller should only be invoked once (second is suppressed as duplicate)
            self.assertEqual(caller.call_count, 1)

    def test_non_duplicate_different_error_is_stored(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            caller = MagicMock(return_value="critique")
            engine = _make_engine(store, caller)

            engine.reflect(_task(), "SyntaxError in line 10", FailureType.VERIFY_FAIL)
            engine.reflect(_task(), "IndentationError in line 99", FailureType.VERIFY_FAIL)

            self.assertEqual(caller.call_count, 2)

    def test_reflect_never_raises_on_bad_caller(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            def bad_caller(p): raise RuntimeError("API unavailable")
            engine = _make_engine(store, bad_caller)
            result = engine.reflect(_task(), "error", FailureType.TEST_FAIL)
            self.assertIsNotNone(result)


# ── PostMortemEngine.recall() ─────────────────────────────────────────────────

class TestRecall(unittest.TestCase):

    def _seed(self, store, file_path, error_type, critique):
        store.save(make_error_pattern(
            task_id="t0", file_path=file_path,
            error_type=error_type, error_msg="err",
            critique=critique,
        ))

    def test_exact_match_returned(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            self._seed(store, "api/auth.py", "TEST_FAIL", "check interface")
            engine = _make_engine(store)
            recalled = engine.recall(_task(), FailureType.TEST_FAIL)
            self.assertEqual(len(recalled), 1)
            self.assertEqual(recalled[0].critique, "check interface")

    def test_file_level_fallback_when_no_exact_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            self._seed(store, "api/auth.py", "WORKER_FAIL", "worker failed here")
            engine = _make_engine(store)
            # Ask for TEST_FAIL but only WORKER_FAIL exists → file-level fallback
            recalled = engine.recall(_task(), FailureType.TEST_FAIL)
            self.assertEqual(len(recalled), 1)

    def test_semantic_fallback_when_no_file_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            # Seed a different file but with matching keywords in critique
            self._seed(store, "other/module.py", "WORKER_FAIL",
                       "auth token dereference caused null pointer in user session")
            engine = _make_engine(store)
            # Task mentions "auth" and "user" — semantic fallback should find it
            recalled = engine.recall(
                {"action": "fix auth token user login", "file": "new/file.py"},
                FailureType.TEST_FAIL,
            )
            self.assertGreater(len(recalled), 0)

    def test_empty_recall_when_store_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            engine = _make_engine(store)
            recalled = engine.recall(_task(), FailureType.VERIFY_FAIL)
            self.assertEqual(recalled, [])


# ── PostMortemEngine.format_for_prompt() ──────────────────────────────────────

class TestFormatForPrompt(unittest.TestCase):

    def test_empty_list_returns_empty_string(self):
        result = PostMortemEngine.format_for_prompt([])
        self.assertEqual(result, "")

    def test_formats_patterns_with_header(self):
        p = make_error_pattern("t1", "api/auth.py", "TEST_FAIL", "err", "fix the None check")
        result = PostMortemEngine.format_for_prompt([p])
        self.assertIn("PAST FAILURE CRITIQUES", result)
        self.assertIn("fix the None check", result)
        self.assertIn("TEST_FAIL", result)

    def test_caps_at_max_recall(self):
        patterns = [
            make_error_pattern(f"t{i}", "file.py", "TEST_FAIL", "err", f"critique {i}")
            for i in range(10)
        ]
        result = PostMortemEngine.format_for_prompt(patterns)
        # Should show at most 3 (default _MAX_RECALL)
        import re
        items = re.findall(r"^\s+\d+\.", result, re.MULTILINE)
        self.assertLessEqual(len(items), 3)


# ── PostMortemEngine.stats() ──────────────────────────────────────────────────

class TestStats(unittest.TestCase):

    def test_empty_store_returns_zeros(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            engine = _make_engine(store)
            s = engine.stats()
            self.assertEqual(s["total_critiques"], 0)
            self.assertEqual(s["files_with_critiques"], 0)

    def test_populated_store_counts_correctly(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            store.save(make_error_pattern("t1", "a.py", "TEST_FAIL", "err", "c1"))
            store.save(make_error_pattern("t2", "a.py", "VERIFY_FAIL", "err", "c2"))
            store.save(make_error_pattern("t3", "b.py", "TEST_FAIL", "err", "c3"))
            engine = _make_engine(store)
            s = engine.stats()
            self.assertEqual(s["total_critiques"], 3)
            self.assertEqual(s["files_with_critiques"], 2)
            self.assertEqual(s["most_failing_file"], "a.py")


# ── LearningInspector Reflexion Stats ────────────────────────────────────────

class TestLearningInspectorReflexion(unittest.TestCase):

    def test_reflexion_stats_with_empty_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            from scaffold.agent.learning_inspector import LearningInspector
            inspector = LearningInspector(awos_dir=tmp)
            stats = inspector._reflexion_stats()
            self.assertIsNotNone(stats)
            self.assertEqual(stats.total_critiques, 0)

    def test_reflexion_stats_populated(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ErrorPatternStore(
                store_path=str(Path(tmp) / "error_patterns.jsonl")
            )
            store.save(make_error_pattern("t1", "a.py", "TEST_FAIL", "err", "c1"))
            store.save(make_error_pattern("t2", "a.py", "TEST_FAIL", "err", "c2"))
            store.save(make_error_pattern("t3", "b.py", "VERIFY_FAIL", "err", "c3"))

            from scaffold.agent.learning_inspector import LearningInspector
            inspector = LearningInspector(awos_dir=tmp)
            stats = inspector._reflexion_stats()

            self.assertEqual(stats.total_critiques, 3)
            self.assertEqual(stats.files_with_critiques, 2)
            self.assertEqual(stats.most_failing_file, "a.py")
            self.assertEqual(stats.most_common_type, "TEST_FAIL")
            self.assertIn("TEST_FAIL", stats.by_failure_type)

    def test_report_includes_reflexion_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            from scaffold.agent.learning_inspector import LearningInspector
            inspector = LearningInspector(awos_dir=tmp)
            report = inspector.report()
            self.assertIsNotNone(report.reflexion)

    def test_render_includes_reflexion_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ErrorPatternStore(
                store_path=str(Path(tmp) / "error_patterns.jsonl")
            )
            store.save(make_error_pattern("t1", "api/auth.py", "TEST_FAIL", "err", "c"))
            from scaffold.agent.learning_inspector import LearningInspector
            inspector = LearningInspector(awos_dir=tmp)
            rendered = inspector.report().render()
            self.assertIn("Reflexion Memory", rendered)
            self.assertIn("Critiques stored", rendered)


if __name__ == "__main__":
    unittest.main()
