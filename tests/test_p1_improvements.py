"""
P1 Tests — Test Execution Verifier, Architect+Editor Split, SessionState
"""
import os
import sys
import tempfile
import textwrap
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from scaffold.agent.verifier import Verifier
from scaffold.agent.session_state import SessionState, TaskSummary, _extract_new_symbols


# ─── Verifier._discover_test_file ────────────────────────────────────────────

class TestDiscoverTestFile:
    def setup_method(self):
        self.v = Verifier()

    def test_discover_standard_pattern(self, tmp_path):
        (tmp_path / "tests").mkdir()
        tf = tmp_path / "tests" / "test_worker.py"
        tf.write_text("import worker\n")
        result = self.v._discover_test_file("scaffold/agent/worker.py", str(tmp_path))
        assert result == str(tf)

    def test_discover_alt_pattern(self, tmp_path):
        (tmp_path / "tests").mkdir()
        tf = tmp_path / "tests" / "worker_test.py"
        tf.write_text("# alt pattern\n")
        result = self.v._discover_test_file("scaffold/agent/worker.py", str(tmp_path))
        assert result == str(tf)

    def test_discover_missing_returns_none(self, tmp_path):
        (tmp_path / "tests").mkdir()
        result = self.v._discover_test_file("scaffold/agent/nonexistent.py", str(tmp_path))
        assert result is None

    def test_discover_grep_fallback(self, tmp_path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        tf = tests_dir / "test_misc.py"
        tf.write_text("import worker\nfrom worker import Worker\n")
        result = self.v._discover_test_file("some/path/worker.py", str(tmp_path))
        assert result == str(tf)

    def test_discover_no_tests_dir_returns_none(self, tmp_path):
        result = self.v._discover_test_file("worker.py", str(tmp_path))
        assert result is None


# ─── Verifier.run_tests (safety-gated) ──────────────────────────────────────

class TestRunTestsSafetyGate:
    def test_no_safety_env_skips_tests(self, tmp_path):
        v = Verifier()
        old = os.environ.pop("AWOS_SAFE_TO_RUN_TESTS", None)
        try:
            (tmp_path / "tests").mkdir()
            tf = tmp_path / "tests" / "test_dummy.py"
            tf.write_text("def test_x(): pass\n")
            result = v.run_tests("dummy.py", str(tmp_path))
            assert result.no_tests_found or "skipped" in result.raw_output.lower()
        finally:
            if old is not None:
                os.environ["AWOS_SAFE_TO_RUN_TESTS"] = old

    def test_run_tests_no_file_fallback(self, tmp_path):
        v = Verifier()
        # No tests dir — should gracefully return no_tests_found
        result = v.run_tests("scaffold/agent/nonexistent_xyz.py", str(tmp_path))
        assert result is not None  # should never raise


# ─── _extract_new_symbols ────────────────────────────────────────────────────

class TestExtractNewSymbols:
    def test_detects_new_function(self):
        old = "def existing(): pass\n"
        new = "def existing(): pass\ndef snapshot(): return {}\n"
        syms = _extract_new_symbols(old, new)
        assert "snapshot" in syms
        assert "existing" not in syms

    def test_detects_new_class(self):
        old = ""
        new = "class ToolTracker:\n    pass\n"
        syms = _extract_new_symbols(old, new)
        assert "ToolTracker" in syms

    def test_no_new_symbols(self):
        code = "def foo(): return 1\n"
        syms = _extract_new_symbols(code, "def foo(): return 2\n")
        assert syms == []

    def test_invalid_syntax_returns_empty(self):
        syms = _extract_new_symbols("valid code", "def broken(: ...")
        assert syms == []


# ─── SessionState ─────────────────────────────────────────────────────────────

class TestSessionState:
    def _make_state(self, n_tasks=3):
        return SessionState(feature_goal="Add logging to worker", total_tasks=n_tasks)

    def _make_task(self, tid, action="add log call", file="worker.py"):
        return {"task_id": tid, "action": action, "file": file}

    def test_empty_state_returns_empty_block(self):
        s = self._make_state()
        assert s.to_context_block() == ""

    def test_context_block_contains_goal_and_task(self):
        s = self._make_state()
        s.record_task(self._make_task(1), {"success": True, "reasoning": "added logger"})
        block = s.to_context_block()
        assert "Add logging to worker" in block
        assert "added logger" in block

    def test_context_block_shows_status_icons(self):
        s = self._make_state()
        s.record_task(self._make_task(1), {"success": True})
        s.record_task(self._make_task(2), {"success": False})
        block = s.to_context_block()
        assert "✅" in block
        assert "❌" in block

    def test_modified_files_tracked(self):
        s = self._make_state()
        s.record_task(self._make_task(1, file="a.py"), {"success": True})
        s.record_task(self._make_task(2, file="b.py"), {"success": True})
        assert "a.py" in s.modified_files
        assert "b.py" in s.modified_files

    def test_duplicate_file_not_added_twice(self):
        s = self._make_state()
        s.record_task(self._make_task(1, file="a.py"), {"success": True})
        s.record_task(self._make_task(2, file="a.py"), {"success": True})
        assert s.modified_files.count("a.py") == 1

    def test_compaction_triggers_at_threshold(self):
        s = self._make_state(n_tasks=20)
        for i in range(3):
            s.record_task(
                self._make_task(i, action=f"action {i}"),
                {"success": True, "reasoning": "done"},
            )
        s.maybe_compact(threshold=1)   # force compaction with low threshold
        assert s.compact_summary != ""
        assert len(s.completed_tasks) == 0   # detailed list cleared

    def test_compaction_fallback_compact_has_file_info(self):
        s = self._make_state(n_tasks=5)
        for i in range(3):
            s.record_task(
                self._make_task(i, action=f"do something {i}", file=f"file{i}.py"),
                {"success": True},
            )
        s.maybe_compact(threshold=1)   # force compaction
        assert "Modified" in s.compact_summary or "file" in s.compact_summary.lower()

    def test_compaction_with_llm_caller(self):
        s = self._make_state(n_tasks=5)
        for i in range(3):
            s.record_task(
                self._make_task(i, action=f"action {i}"),
                {"success": True},
            )
        called = []
        def fake_llm(prompt, max_tokens):
            called.append(prompt)
            return "• Did stuff\n• Modified files"
        s.maybe_compact(fake_llm, threshold=1)   # force compaction
        assert len(called) == 1
        assert s.compact_summary == "• Did stuff\n• Modified files"

    def test_small_state_no_compaction(self):
        s = self._make_state(n_tasks=3)
        s.record_task(self._make_task(1), {"success": True})
        s.maybe_compact()
        assert s.compact_summary == ""   # too small to compact
        assert len(s.completed_tasks) == 1
