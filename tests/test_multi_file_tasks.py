"""
Tests for multi-file tasks: scaffold/agent/task_schema.py and the scheduling,
planning and validation that depend on it.

The planner used to forbid a task from touching more than one file, which
excluded the commonest real change — edit a function, update its callers, fix
the test. Lifting that is additive: `file` keeps meaning "the primary file" for
the ~30 call sites that read it, and `files` carries the whole set.

  TestTaskFiles        — the shared accessor, on both task shapes
  TestNormalisation    — `file` and `files` are reconciled once, at the boundary
  TestScheduling       — tasks sharing ANY file are never run concurrently
  TestPlannerGating    — multi-file is opt-in, because single-shot cannot do it
  TestBackCompat       — single-file tasks behave exactly as before
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scaffold"))

from scaffold.agent.dag_executor import DAGExecutor
from scaffold.agent.task_schema import (
    describe_files,
    is_multi_file,
    normalise_task,
    primary_file,
    task_files,
)


def _task(task_id, file=None, files=None, **extra):
    task = {"task_id": task_id, "action": "do a thing", "complexity": "low"}
    if file is not None:
        task["file"] = file
    if files is not None:
        task["files"] = files
    task.update(extra)
    return task


class TestTaskFiles(unittest.TestCase):
    def test_single_file_task(self):
        self.assertEqual(task_files(_task(1, file="a.py")), ["a.py"])

    def test_multi_file_task_keeps_primary_first(self):
        task = _task(1, file="a.py", files=["a.py", "b.py", "c.py"])
        self.assertEqual(task_files(task), ["a.py", "b.py", "c.py"])

    def test_primary_leads_even_when_files_disagrees_on_order(self):
        task = _task(1, file="b.py", files=["a.py", "b.py"])
        self.assertEqual(task_files(task), ["b.py", "a.py"])

    def test_duplicates_collapse(self):
        task = _task(1, file="a.py", files=["a.py", "a.py", "b.py"])
        self.assertEqual(task_files(task), ["a.py", "b.py"])

    def test_missing_file_yields_empty_not_a_placeholder(self):
        """`[""]` would look like a real path to every caller downstream."""
        self.assertEqual(task_files(_task(1)), [])

    def test_blank_and_whitespace_paths_are_dropped(self):
        self.assertEqual(task_files(_task(1, file="  ", files=["", "  "])), [])

    def test_paths_are_stripped(self):
        self.assertEqual(task_files(_task(1, file=" a.py ")), ["a.py"])

    def test_files_given_as_a_bare_string(self):
        self.assertEqual(task_files(_task(1, files="a.py")), ["a.py"])

    def test_files_only_without_primary(self):
        self.assertEqual(task_files(_task(1, files=["a.py", "b.py"])), ["a.py", "b.py"])

    def test_non_string_entries_are_ignored(self):
        self.assertEqual(task_files(_task(1, file="a.py", files=[None, 3, "b.py"])), ["a.py", "b.py"])

    def test_primary_file_helper(self):
        self.assertEqual(primary_file(_task(1, file="a.py", files=["a.py", "b.py"])), "a.py")
        self.assertEqual(primary_file(_task(1)), "")

    def test_is_multi_file(self):
        self.assertFalse(is_multi_file(_task(1, file="a.py")))
        self.assertTrue(is_multi_file(_task(1, file="a.py", files=["a.py", "b.py"])))

    def test_describe_files_is_readable(self):
        self.assertEqual(describe_files(_task(1, file="a.py")), "a.py")
        self.assertIn("+1 more", describe_files(_task(1, file="a.py", files=["a.py", "b.py"])))
        self.assertEqual(describe_files(_task(1)), "(no file)")


class TestNormalisation(unittest.TestCase):
    def test_single_file_task_gains_no_files_key(self):
        """A single-file plan stays byte-identical to the pre-change output."""
        self.assertNotIn("files", normalise_task(_task(1, file="a.py")))

    def test_multi_file_task_keeps_both_keys_consistent(self):
        result = normalise_task(_task(1, file="b.py", files=["a.py", "b.py"]))
        self.assertEqual(result["file"], "b.py")
        self.assertEqual(result["files"], ["b.py", "a.py"])

    def test_files_only_task_gains_a_primary(self):
        result = normalise_task(_task(1, files=["a.py", "b.py"]))
        self.assertEqual(result["file"], "a.py")

    def test_redundant_files_list_is_dropped(self):
        result = normalise_task(_task(1, file="a.py", files=["a.py"]))
        self.assertNotIn("files", result)

    def test_does_not_mutate_the_input(self):
        original = _task(1, files=["a.py", "b.py"])
        normalise_task(original)
        self.assertNotIn("file", original)

    def test_other_fields_survive(self):
        result = normalise_task(_task(1, file="a.py", constraints=["x"], complexity="high"))
        self.assertEqual(result["constraints"], ["x"])
        self.assertEqual(result["complexity"], "high")


class TestScheduling(unittest.TestCase):
    """
    DAGExecutor runs a wave concurrently, so two tasks in one wave must never
    touch a common file. It previously keyed on the primary file only, which
    would put overlapping multi-file tasks in the same wave and let two threads
    write one file at once.
    """

    def _wave_index(self, waves, task_id):
        for index, wave in enumerate(waves):
            if any(t["task_id"] == task_id for t in wave):
                return index
        raise AssertionError(f"task {task_id} missing from every wave")

    def test_overlap_on_a_secondary_file_is_serialised(self):
        tasks = [
            _task(1, file="auth.py", files=["auth.py", "test_auth.py"]),
            _task(2, file="test_auth.py"),
        ]
        waves = DAGExecutor._build_waves(tasks)
        self.assertNotEqual(
            self._wave_index(waves, 1),
            self._wave_index(waves, 2),
            "tasks sharing test_auth.py were scheduled concurrently",
        )

    def test_overlap_between_two_multi_file_tasks_is_serialised(self):
        tasks = [
            _task(1, file="a.py", files=["a.py", "shared.py"]),
            _task(2, file="b.py", files=["b.py", "shared.py"]),
        ]
        waves = DAGExecutor._build_waves(tasks)
        self.assertNotEqual(self._wave_index(waves, 1), self._wave_index(waves, 2))

    def test_disjoint_multi_file_tasks_still_run_in_parallel(self):
        tasks = [
            _task(1, file="a.py", files=["a.py", "a_test.py"]),
            _task(2, file="b.py", files=["b.py", "b_test.py"]),
        ]
        waves = DAGExecutor._build_waves(tasks)
        self.assertEqual(self._wave_index(waves, 1), self._wave_index(waves, 2))

    def test_no_task_appears_twice_in_a_wave(self):
        """A task listed under several files must not be scheduled repeatedly."""
        tasks = [_task(1, file="a.py", files=["a.py", "b.py", "c.py"])]
        waves = DAGExecutor._build_waves(tasks)
        ids = [t["task_id"] for wave in waves for t in wave]
        self.assertEqual(ids, [1])

    def test_duplicate_task_ids_do_not_report_a_false_cycle(self):
        """
        A malformed plan repeating a task_id used to give that task a
        dependency on itself, which the scheduler then reported as a cycle and
        force-scheduled. The work still ran, but the warning pointed at a
        problem that did not exist.
        """
        tasks = [_task(1, file="a.py"), _task(1, file="a.py")]
        with self.assertLogs("scaffold.agent.dag_executor", level="WARNING") as logged:
            # assertLogs fails outright when nothing is logged, so emit a
            # sentinel and then assert the cycle warning is not among them.
            import logging

            logging.getLogger("scaffold.agent.dag_executor").warning("sentinel")
            waves = DAGExecutor._build_waves(tasks)

        self.assertFalse(
            [line for line in logged.output if "Cycle detected" in line],
            "a duplicate task_id produced a self-dependency and a false cycle",
        )
        self.assertEqual([t["task_id"] for wave in waves for t in wave], [1])

    def test_every_task_is_scheduled_exactly_once(self):
        tasks = [
            _task(1, file="a.py", files=["a.py", "shared.py"]),
            _task(2, file="b.py", files=["b.py", "shared.py"]),
            _task(3, file="c.py"),
        ]
        waves = DAGExecutor._build_waves(tasks)
        ids = sorted(t["task_id"] for wave in waves for t in wave)
        self.assertEqual(ids, [1, 2, 3])

    def test_explicit_dependencies_still_apply(self):
        tasks = [_task(1, file="a.py"), _task(2, file="b.py", depends_on=[1])]
        waves = DAGExecutor._build_waves(tasks)
        self.assertLess(self._wave_index(waves, 1), self._wave_index(waves, 2))

    def test_single_file_scheduling_is_unchanged(self):
        tasks = [_task(1, file="a.py"), _task(2, file="a.py"), _task(3, file="b.py")]
        waves = DAGExecutor._build_waves(tasks)
        self.assertNotEqual(self._wave_index(waves, 1), self._wave_index(waves, 2))
        self.assertEqual(self._wave_index(waves, 1), self._wave_index(waves, 3))

    def test_tasks_without_files_do_not_all_collide(self):
        """An absent file once keyed every such task to "", serialising them."""
        waves = DAGExecutor._build_waves([_task(1), _task(2)])
        self.assertEqual(self._wave_index(waves, 1), self._wave_index(waves, 2))


class TestPlannerGating(unittest.TestCase):
    """Multi-file must be opt-in: the single-shot Worker cannot execute one."""

    def _planner(self, **kwargs):
        from scaffold.agent.planner import Planner

        os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
        return Planner(**kwargs)

    def test_defaults_to_off(self):
        os.environ.pop("AWOS_MULTI_FILE_TASKS", None)
        self.assertFalse(self._planner().allow_multi_file)

    def test_enabled_by_argument(self):
        self.assertTrue(self._planner(allow_multi_file=True).allow_multi_file)

    def test_enabled_by_environment(self):
        os.environ["AWOS_MULTI_FILE_TASKS"] = "1"
        try:
            self.assertTrue(self._planner().allow_multi_file)
        finally:
            os.environ.pop("AWOS_MULTI_FILE_TASKS", None)

    def test_argument_overrides_environment(self):
        os.environ["AWOS_MULTI_FILE_TASKS"] = "1"
        try:
            self.assertFalse(self._planner(allow_multi_file=False).allow_multi_file)
        finally:
            os.environ.pop("AWOS_MULTI_FILE_TASKS", None)

    def test_prompt_forbids_multiple_files_when_disabled(self):
        planner = self._planner(allow_multi_file=False)
        self.assertIn("ONLY ONE file", planner._constraints_block())
        self.assertNotIn("files", planner._schema_file_fields())

    def test_prompt_permits_and_bounds_multiple_files_when_enabled(self):
        planner = self._planner(allow_multi_file=True)
        block = planner._constraints_block()
        self.assertIn("more than one file", block)
        # It must not become licence to bundle unrelated edits.
        self.assertIn("same unit of work", block)
        self.assertIn('"files"', planner._schema_file_fields())


class TestBackCompat(unittest.TestCase):
    """The ~30 existing readers of task["file"] must keep working untouched."""

    def test_primary_file_is_always_present_after_normalisation(self):
        for task in (
            _task(1, file="a.py"),
            _task(2, files=["a.py", "b.py"]),
            _task(3, file="a.py", files=["a.py", "b.py"]),
        ):
            with self.subTest(task=task["task_id"]):
                self.assertTrue(normalise_task(task)["file"])

    def test_a_legacy_task_reads_identically(self):
        legacy = _task(1, file="a.py")
        self.assertEqual(legacy["file"], "a.py")
        self.assertEqual(task_files(legacy), ["a.py"])
        self.assertEqual(normalise_task(legacy), legacy)


if __name__ == "__main__":
    unittest.main()
