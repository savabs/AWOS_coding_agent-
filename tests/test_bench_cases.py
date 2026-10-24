"""
Tests for scaffold/agent/bench_cases.py and the shipped benchmark cases.

The cases are the measuring instrument. If one of them is wrong — tests that
pass while the bug is still there, or a multi-file case solvable without its
context — every number the benchmark produces is wrong in a way that looks like
signal. These tests guard the instrument.

  TestLoading         — a case reads off disk correctly
  TestStaging         — a staged case is runnable and isolated
  TestScoring         — tests fail on buggy, pass on fix
  TestShippedCases    — every case in tests/bug_cases is sound
  TestDiscrimination  — multi-file cases genuinely need their context
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scaffold"))

from scaffold.agent.bench_cases import (
    discover_cases,
    load_case,
    run_case_tests,
    stage_case,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = REPO_ROOT / "tests" / "bug_cases"


def _all_cases():
    return [load_case(d) for d in discover_cases(CASES_DIR)]


class TestLoading(unittest.TestCase):
    def test_discovers_the_shipped_cases(self):
        self.assertGreaterEqual(len(discover_cases(CASES_DIR)), 12)

    def test_missing_directory_is_empty_not_an_error(self):
        self.assertEqual(discover_cases("/nonexistent/cases"), [])

    def test_case_exposes_its_task(self):
        case = load_case(CASES_DIR / "a1_off_by_one_page")
        self.assertEqual(case.target_file, "buggy.py")
        self.assertIn("paginate", case.action)
        self.assertFalse(case.is_multi_file)

    def test_context_is_loaded_for_multi_file_cases(self):
        case = load_case(CASES_DIR / "b1_constant_mismatch")
        self.assertTrue(case.is_multi_file)
        self.assertIn("limits.py", case.context)
        self.assertIn("MAX_RETRIES", case.context["limits.py"])

    def test_tier_comes_from_the_name(self):
        self.assertEqual(load_case(CASES_DIR / "b1_constant_mismatch").tier, "B")


class TestStaging(unittest.TestCase):
    def test_stages_target_tests_and_context(self):
        case = load_case(CASES_DIR / "b1_constant_mismatch")
        with tempfile.TemporaryDirectory() as tmp:
            staged = stage_case(case, tmp)
            self.assertTrue((staged / "buggy.py").exists())
            self.assertTrue((staged / "test_case.py").exists())
            self.assertTrue((staged / "limits.py").exists())
            self.assertTrue((staged / "pytest.ini").exists())

    def test_source_override_replaces_the_target(self):
        case = load_case(CASES_DIR / "a1_off_by_one_page")
        with tempfile.TemporaryDirectory() as tmp:
            staged = stage_case(case, tmp, source="# replaced\n")
            self.assertEqual((staged / "buggy.py").read_text(), "# replaced\n")

    def test_local_pytest_ini_shields_from_repo_config(self):
        """The repo's pytest.ini sets --timeout and -m, which a case cannot assume."""
        case = load_case(CASES_DIR / "a1_off_by_one_page")
        with tempfile.TemporaryDirectory() as tmp:
            staged = stage_case(case, tmp)
            self.assertEqual((staged / "pytest.ini").read_text().strip(), "[pytest]")


class TestScoring(unittest.TestCase):
    def test_buggy_source_fails(self):
        passed, _ = run_case_tests(load_case(CASES_DIR / "a1_off_by_one_page"))
        self.assertFalse(passed)

    def test_fix_source_passes(self):
        case = load_case(CASES_DIR / "a1_off_by_one_page")
        passed, output = run_case_tests(case, source=case.fix_source)
        self.assertTrue(passed, output[-500:])

    def test_syntactically_broken_source_fails_rather_than_raising(self):
        case = load_case(CASES_DIR / "a1_off_by_one_page")
        passed, _ = run_case_tests(case, source="def paginate(  # unclosed\n")
        self.assertFalse(passed)

    def test_runs_are_isolated_from_each_other(self):
        case = load_case(CASES_DIR / "a1_off_by_one_page")
        run_case_tests(case, source=case.fix_source)
        passed, _ = run_case_tests(case)  # back to the buggy source
        self.assertFalse(passed, "a previous run leaked into this one")


class TestShippedCases(unittest.TestCase):
    """Every shipped case must be a valid measurement."""

    def test_every_case_has_the_required_files(self):
        for case_dir in discover_cases(CASES_DIR):
            for required in ("buggy.py", "fix.py", "test_case.py", "task.json"):
                self.assertTrue(
                    (case_dir / required).exists(), f"{case_dir.name} lacks {required}"
                )

    def test_every_case_fails_before_the_fix(self):
        for case in _all_cases():
            with self.subTest(case=case.name):
                passed, _ = run_case_tests(case)
                self.assertFalse(
                    passed, f"{case.name}: tests pass on buggy.py — it measures nothing"
                )

    def test_every_case_passes_after_the_fix(self):
        for case in _all_cases():
            with self.subTest(case=case.name):
                passed, output = run_case_tests(case, source=case.fix_source)
                self.assertTrue(
                    passed, f"{case.name}: unsolvable even with fix.py\n{output[-400:]}"
                )

    def test_buggy_and_fix_differ(self):
        for case in _all_cases():
            with self.subTest(case=case.name):
                self.assertNotEqual(case.buggy_source, case.fix_source)

    def test_tiers_are_all_represented(self):
        tiers = {case.tier for case in _all_cases()}
        self.assertEqual(tiers, {"A", "B", "C", "D"})

    def test_both_self_contained_and_multi_file_cases_exist(self):
        cases = _all_cases()
        self.assertTrue(any(c.is_multi_file for c in cases))
        self.assertTrue(any(not c.is_multi_file for c in cases))


class TestDiscrimination(unittest.TestCase):
    """
    The benchmark's premise: a multi-file case cannot be solved from buggy.py
    alone. If it could, both executors would score the same and the comparison
    would prove nothing.
    """

    def _required_names(self, case):
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "validator", REPO_ROOT / "scripts" / "validate_bug_cases.py"
        )
        validator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(validator)

        names = set()
        for source in case.context.values():
            names |= validator._defined_names(source)
        return {n for n in names if n in case.fix_source and n not in case.buggy_source}

    def test_each_multi_file_case_needs_something_only_context_provides(self):
        multi = [c for c in _all_cases() if c.is_multi_file]
        self.assertGreaterEqual(len(multi), 6)
        for case in multi:
            with self.subTest(case=case.name):
                required = self._required_names(case)
                self.assertTrue(
                    required,
                    f"{case.name}: fix.py uses nothing from context/, so a "
                    "single-shot worker could solve it without looking",
                )

    def test_the_required_knowledge_is_absent_from_the_target_file(self):
        """Spot-check: the constant's value appears only in the context file."""
        case = load_case(CASES_DIR / "b1_constant_mismatch")
        self.assertNotIn("MAX_RETRIES", case.buggy_source)
        self.assertIn("MAX_RETRIES", case.context["limits.py"])

    def test_task_description_does_not_leak_the_answer(self):
        """A task that states the value outright would defeat its own case."""
        case = load_case(CASES_DIR / "b1_constant_mismatch")
        self.assertNotIn("7", case.action)


if __name__ == "__main__":
    unittest.main()
