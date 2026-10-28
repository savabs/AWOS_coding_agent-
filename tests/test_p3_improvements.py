"""
tests/test_p3_improvements.py — Tests for P3 SOTA improvements.

Covers:
    P3.1 — LintDiagnostic, RuffLinter.format_for_prompt, DiagnosticPipeline
    P3.2 — SkillExtractor, SkillLibrary
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from scaffold.agent.lint_pipeline import (
    LintDiagnostic, RuffLinter, DiagnosticPipeline, BLOCKING_CODES,
)
from scaffold.agent.skill_extractor import SkillExtractor, SkillLibrary


# ── P3.1 Lint Pipeline ────────────────────────────────────────────────────────

class TestLintDiagnostic(unittest.TestCase):

    def test_dataclass_fields(self):
        d = LintDiagnostic(file="a.py", line=5, col=3, code="E501",
                           message="line too long", severity="warning")
        self.assertEqual(d.code, "E501")
        self.assertEqual(d.line, 5)

    def test_blocking_codes_set(self):
        self.assertIn("E999", BLOCKING_CODES)
        self.assertIn("F821", BLOCKING_CODES)
        self.assertIn("F811", BLOCKING_CODES)


class TestRuffLinterFormat(unittest.TestCase):

    def test_format_empty(self):
        linter = RuffLinter()
        result = linter.format_for_prompt([])
        self.assertEqual(result, "")

    def test_format_with_diagnostics(self):
        linter = RuffLinter()
        diags = [
            LintDiagnostic(file="a.py", line=10, col=1, code="F821",
                           message="undefined name 'foo'", severity="error"),
        ]
        result = linter.format_for_prompt(diags)
        self.assertIn("F821", result)
        self.assertIn("Line 10", result)
        self.assertIn("Fix these issues", result)

    def test_ruff_lint_missing_ruff_silent(self):
        linter = RuffLinter()
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = linter.lint_file("/nonexistent/file.py")
        self.assertEqual(result, [])

    def test_ruff_lint_timeout_silent(self):
        import subprocess
        linter = RuffLinter()
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ruff", 10)):
            result = linter.lint_file("/nonexistent/file.py")
        self.assertEqual(result, [])


class TestDiagnosticPipeline(unittest.TestCase):

    def test_has_blocking_errors_true(self):
        pipeline = DiagnosticPipeline(use_pyright=False)
        diags = [LintDiagnostic("a.py", 1, 0, "E999", "syntax error", "error")]
        self.assertTrue(pipeline.has_blocking_errors(diags))

    def test_has_blocking_errors_false(self):
        pipeline = DiagnosticPipeline(use_pyright=False)
        diags = [LintDiagnostic("a.py", 1, 0, "E501", "line too long", "warning")]
        self.assertFalse(pipeline.has_blocking_errors(diags))

    def test_check_on_valid_python_returns_list(self):
        with tempfile.TemporaryDirectory() as td:
            pipeline = DiagnosticPipeline(use_pyright=False)
            diags = pipeline.check(
                os.path.join(td, "test.py"),
                "x = 1\n",
                project_root=td,
            )
            self.assertIsInstance(diags, list)

    def test_format_delegates_to_ruff(self):
        pipeline = DiagnosticPipeline(use_pyright=False)
        diags = [LintDiagnostic("a.py", 5, 0, "F821", "undefined name", "error")]
        result = pipeline.format_for_prompt(diags)
        self.assertIn("F821", result)


# ── P3.2 Skill Extractor + Library ───────────────────────────────────────────

class TestSkillExtractor(unittest.TestCase):

    def _make_extractor(self, tmpdir):
        ext = SkillExtractor()
        ext.SKILLS_DIR = os.path.join(tmpdir, "skills")
        return ext

    def test_low_score_no_extraction(self):
        with tempfile.TemporaryDirectory() as td:
            ext = self._make_extractor(td)
            result = ext.maybe_extract_skill(
                task={"action": "fix bug", "file": "a.py", "complexity": "low"},
                result={"score": 0.5},
                cheap_llm_caller=None,
            )
            self.assertIsNone(result)

    def test_high_score_creates_skill_file(self):
        with tempfile.TemporaryDirectory() as td:
            ext = self._make_extractor(td)
            result = ext.maybe_extract_skill(
                task={"action": "add login method", "file": "auth.py", "complexity": "medium"},
                result={"score": 0.95, "key_change": "added login"},
                cheap_llm_caller=None,  # uses fallback
            )
            self.assertIsNotNone(result)
            self.assertTrue(Path(result).exists())

    def test_duplicate_key_updates_existing(self):
        with tempfile.TemporaryDirectory() as td:
            ext = self._make_extractor(td)
            task = {"action": "add login method", "file": "auth.py", "complexity": "medium"}
            ext.maybe_extract_skill(task, {"score": 0.95}, cheap_llm_caller=None)
            result2 = ext.maybe_extract_skill(task, {"score": 0.95}, cheap_llm_caller=None)
            self.assertIsNotNone(result2)

    def test_skill_key_deterministic(self):
        task = {"action": "fix bug in `worker.py`"}
        ext = SkillExtractor()
        k1 = ext._compute_skill_key(task)
        k2 = ext._compute_skill_key(task)
        self.assertEqual(k1, k2)

    def test_skill_key_strips_symbols(self):
        t1 = {"action": "add method to `FooBar`"}
        t2 = {"action": "add method to `BazQux`"}
        ext = SkillExtractor()
        self.assertEqual(ext._compute_skill_key(t1), ext._compute_skill_key(t2))


class TestSkillLibrary(unittest.TestCase):

    def _make_library(self, tmpdir):
        lib = SkillLibrary()
        lib.SKILLS_DIR = os.path.join(tmpdir, "skills")
        return lib

    def test_no_skills_dir_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            lib = self._make_library(td)
            result = lib.get_relevant_skills({"action": "fix bug"})
            self.assertEqual(result, [])

    def test_relevant_skills_by_overlap(self):
        with tempfile.TemporaryDirectory() as td:
            skills_dir = Path(td) / "skills"
            skills_dir.mkdir()
            (skills_dir / "login_abc123.md").write_text(
                "---\ntitle: \"add login\"\ntags: [login, auth]\n---\n\n## When to use\nLogin tasks."
            )
            lib = self._make_library(td)
            skills = lib.get_relevant_skills({"action": "add login handler"}, top_k=1)
            self.assertEqual(len(skills), 1)
            self.assertIn("login", skills[0].lower())

    def test_format_for_prompt_empty(self):
        lib = SkillLibrary()
        result = lib.format_for_prompt([])
        self.assertEqual(result, "")

    def test_format_for_prompt_with_skills(self):
        lib = SkillLibrary()
        result = lib.format_for_prompt(["## When to use\nDo the thing."])
        self.assertIn("Skill 1", result)
        self.assertIn("Do the thing", result)


if __name__ == "__main__":
    unittest.main()
