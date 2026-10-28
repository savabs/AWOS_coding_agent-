import pytest
pytestmark = pytest.mark.integration

"""
Adversarial test harness for the Orchestrator → Worker → Verifier pipeline.

Principle: tests are designed to BREAK the agent, not confirm it works.
The agent never sees the test suite — it only sees the task. After execution,
the existing test suite acts as the adversary.

What this tests (that happy-path tests miss):
  1. Regression gate: agent's change must not break any pre-existing test
  2. Diff minimality: agent must not touch code unrelated to the task
  3. Dependency gate: agent must not introduce new imports/dependencies
  4. Negative-space: agent must NOT do things it wasn't asked to do
  5. Ambiguity resistance: agent must handle underspecified tasks correctly
  6. Composition: sequential tasks must not conflict
"""

import sys
import os
import subprocess
import difflib
import ast
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scaffold"))
os.environ.setdefault("AWOS_MONTHLY_BUDGET", "20.0")
os.environ.setdefault("ANTHROPIC_API_KEY", "fake-key-for-testing")
os.environ.setdefault("DEEPSEEK_API_KEY", "fake-key-for-testing")


# ═══════════════════════════════════════════════════════════════════════════════
# Test project: math_utils module with edge-case tests the agent might break
# ═══════════════════════════════════════════════════════════════════════════════

MATH_UTILS_CODE = '''"""Math utility functions — deliberately has edge cases."""
from typing import Optional, Union


def divide(a: float, b: float) -> float:
    """Divide a by b."""
    return a / b


def safe_divide(a: float, b: float) -> Optional[float]:
    """Divide a by b, returning None on zero division."""
    if b == 0:
        return None
    return a / b


def factorial(n: int) -> int:
    """Compute factorial of n."""
    if n < 0:
        raise ValueError("n must be non-negative")
    result = 1
    for i in range(2, n + 1):
        result *= i
    return result


def mean(values: list) -> float:
    """Compute arithmetic mean."""
    if not values:
        raise ValueError("values must not be empty")
    return sum(values) / len(values)
'''

MATH_UTILS_TESTS = '''"""Tests for math_utils — includes edge cases the agent might break."""
import pytest
from math_utils import divide, safe_divide, factorial, mean


class TestDivide:
    def test_positive_numbers(self):
        assert divide(10, 2) == 5.0
    def test_negative_numbers(self):
        assert divide(-10, 2) == -5.0
    def test_zero_numerator(self):
        assert divide(0, 5) == 0.0
    def test_zero_denominator_raises(self):
        with pytest.raises(ZeroDivisionError):
            divide(1, 0)
    def test_float_precision(self):
        assert abs(divide(1, 3) - 0.3333333333333333) < 1e-10


class TestSafeDivide:
    def test_normal_division(self):
        assert safe_divide(10, 2) == 5.0
    def test_zero_denominator_returns_none(self):
        assert safe_divide(1, 0) is None
    def test_zero_numerator(self):
        assert safe_divide(0, 5) == 0.0


class TestFactorial:
    def test_factorial_zero(self):
        assert factorial(0) == 1
    def test_factorial_one(self):
        assert factorial(1) == 1
    def test_factorial_five(self):
        assert factorial(5) == 120
    def test_factorial_negative_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            factorial(-1)
    def test_factorial_large(self):
        assert factorial(10) == 3628800


class TestMean:
    def test_simple_mean(self):
        assert mean([1, 2, 3]) == 2.0
    def test_single_value(self):
        assert mean([42]) == 42.0
    def test_empty_raises(self):
        with pytest.raises(ValueError, match="empty"):
            mean([])
    def test_negative_values(self):
        assert mean([-1, 1]) == 0.0
    def test_float_values(self):
        assert mean([1.5, 2.5]) == 2.0
'''


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _make_temp_project(tmp_path: Path) -> Path:
    """Create a temp project with math_utils.py and its test suite."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "__init__.py").write_text("")
    (src / "math_utils.py").write_text(MATH_UTILS_CODE)

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "__init__.py").write_text("")
    (tests_dir / "test_math_utils.py").write_text(MATH_UTILS_TESTS)

    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n")
    return tmp_path


def _run_tests(project_root: Path) -> tuple:
    """Run pytest. Returns (all_pass: bool, output: str)."""
    result = subprocess.run(
        ["pytest", "-q", "--tb=short"],
        cwd=str(project_root),
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "PYTHONPATH": f"{project_root}/src"},
    )
    output = result.stdout + "\n" + result.stderr
    return result.returncode == 0, output.strip()


def _get_diff(original: str, modified: str) -> str:
    return "\n".join(difflib.unified_diff(
        original.splitlines(), modified.splitlines(),
        fromfile="original", tofile="modified", lineterm="",
    ))


def _count_changed_lines(diff_text: str) -> int:
    return sum(1 for line in diff_text.splitlines()
               if (line.startswith("+") or line.startswith("-"))
               and not line.startswith("+++") and not line.startswith("---"))


def _extract_imports(code: str) -> set:
    imports = set()
    try:
        for node in ast.walk(ast.parse(code)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module.split(".")[0])
    except SyntaxError:
        pass
    return imports


# ═══════════════════════════════════════════════════════════════════════════════
# Test: Regression Gate
# ═══════════════════════════════════════════════════════════════════════════════

class TestRegressionGate:
    """Agent's change must not break ANY pre-existing test."""

    def test_add_function_preserves_all_existing_tests(self, tmp_path):
        project = _make_temp_project(tmp_path)
        src_file = project / "src" / "math_utils.py"
        original = src_file.read_text()

        pre_pass, pre_output = _run_tests(project)
        assert pre_pass, f"Pre-existing tests must pass:\n{pre_output}"

        mock_result = {
            "success": True,
            "search": 'def mean(values: list) -> float:\n    """Compute arithmetic mean."""\n    if not values:\n        raise ValueError("values must not be empty")\n    return sum(values) / len(values)',
            "replace": 'def mean(values: list) -> float:\n    """Compute arithmetic mean."""\n    if not values:\n        raise ValueError("values must not be empty")\n    return sum(values) / len(values)\n\n\ndef median(values: list) -> float:\n    """Compute median of values."""\n    if not values:\n        raise ValueError("values must not be empty")\n    sorted_vals = sorted(values)\n    n = len(sorted_vals)\n    mid = n // 2\n    if n % 2 == 0:\n        return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2\n    return sorted_vals[mid]',
            "reasoning": "Added median function after mean",
        }

        pre_planned = [{
            "task_id": 1, "file": "src/math_utils.py",
            "action": "Add a median function that computes the median of a list",
            "complexity": "medium",
            "constraints": ["handle empty list with ValueError", "handle even and odd length"],
            "must_not": ["modify existing functions", "import new libraries"],
        }]

        with patch("agent.worker.Worker.execute_task", return_value=mock_result), \
             patch("agent.core.performance_tracker.ToolPerformanceTracker._load"):
            from agent.orchestrator import Orchestrator
            orch = Orchestrator()
            orch.execute_feature(
                goal="add median function",
                codebase_root=str(project),
                pre_planned_tasks=pre_planned,
            )

        post_pass, post_output = _run_tests(project)
        assert post_pass, (
            f"AGENT BROKE EXISTING TESTS:\n{post_output}\n\n"
            f"Diff:\n{_get_diff(original, src_file.read_text())}"
        )
        assert "def median" in src_file.read_text()

    def test_bug_fix_doesnt_break_other_functions(self, tmp_path):
        project = _make_temp_project(tmp_path)
        src_file = project / "src" / "math_utils.py"
        original = src_file.read_text()

        pre_pass, pre_output = _run_tests(project)
        assert pre_pass

        mock_result = {
            "success": True,
            "search": 'def factorial(n: int) -> int:\n    """Compute factorial of n."""\n    if n < 0:\n        raise ValueError("n must be non-negative")\n    result = 1\n    for i in range(2, n + 1):\n        result *= i\n    return result',
            "replace": 'def factorial(n: int) -> int:\n    """Compute factorial of n."""\n    if n < 0:\n        raise ValueError("n must be non-negative")\n    if n <= 1:\n        return 1\n    result = 1\n    for i in range(2, n + 1):\n        result *= i\n    return result',
            "reasoning": "Added explicit base case for n<=1",
        }

        pre_planned = [{
            "task_id": 1, "file": "src/math_utils.py",
            "action": "Fix factorial function — ensure correct for edge cases",
            "complexity": "low",
            "must_not": ["break existing behavior", "change function signature"],
        }]

        with patch("agent.worker.Worker.execute_task", return_value=mock_result), \
             patch("agent.core.performance_tracker.ToolPerformanceTracker._load"):
            from agent.orchestrator import Orchestrator
            orch = Orchestrator()
            orch.execute_feature(
                goal="fix factorial edge cases",
                codebase_root=str(project),
                pre_planned_tasks=pre_planned,
            )

        post_pass, post_output = _run_tests(project)
        assert post_pass, (
            f"AGENT BROKE TESTS DURING BUG FIX:\n{post_output}\n\n"
            f"Diff:\n{_get_diff(original, src_file.read_text())}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Test: Diff Minimality
# ═══════════════════════════════════════════════════════════════════════════════

class TestDiffMinimality:
    """Agent must only touch code related to the task."""

    def test_single_function_add_is_minimal_diff(self, tmp_path):
        project = _make_temp_project(tmp_path)
        src_file = project / "src" / "math_utils.py"
        original = src_file.read_text()

        mock_result = {
            "success": True,
            "search": 'def mean(values: list) -> float:\n    """Compute arithmetic mean."""\n    if not values:\n        raise ValueError("values must not be empty")\n    return sum(values) / len(values)',
            "replace": 'def mean(values: list) -> float:\n    """Compute arithmetic mean."""\n    if not values:\n        raise ValueError("values must not be empty")\n    return sum(values) / len(values)\n\n\ndef variance(values: list) -> float:\n    """Compute population variance."""\n    if not values:\n        raise ValueError("values must not be empty")\n    m = sum(values) / len(values)\n    return sum((x - m) ** 2 for x in values) / len(values)',
            "reasoning": "Added variance function",
        }

        pre_planned = [{
            "task_id": 1, "file": "src/math_utils.py",
            "action": "Add a variance function",
            "complexity": "medium",
            "must_not": ["modify existing functions", "change imports"],
        }]

        with patch("agent.worker.Worker.execute_task", return_value=mock_result), \
             patch("agent.core.performance_tracker.ToolPerformanceTracker._load"):
            from agent.orchestrator import Orchestrator
            orch = Orchestrator()
            orch.execute_feature(
                goal="add variance function",
                codebase_root=str(project),
                pre_planned_tasks=pre_planned,
            )

        diff = _get_diff(original, src_file.read_text())
        changed_lines = _count_changed_lines(diff)
        assert changed_lines <= 20, (
            f"Diff too large ({changed_lines} lines). Agent may be refactoring:\n{diff}"
        )

    def test_no_unrelated_code_touched(self, tmp_path):
        project = _make_temp_project(tmp_path)
        src_file = project / "src" / "math_utils.py"
        original = src_file.read_text()

        mock_result = {
            "success": True,
            "search": 'def divide(a: float, b: float) -> float:\n    """Divide a by b."""\n    return a / b',
            "replace": 'def divide(a: float, b: float) -> float:\n    """Divide a by b.\n\n    Args:\n        a: numerator\n        b: denominator (must be non-zero)\n\n    Returns:\n        float: result of a / b\n\n    Raises:\n        ZeroDivisionError: if b is zero\n    """\n    return a / b',
            "reasoning": "Expanded docstring for divide",
        }

        pre_planned = [{
            "task_id": 1, "file": "src/math_utils.py",
            "action": "Add detailed docstring to the divide function only",
            "complexity": "low",
            "must_not": ["modify any other function", "change any function body"],
        }]

        with patch("agent.worker.Worker.execute_task", return_value=mock_result), \
             patch("agent.core.performance_tracker.ToolPerformanceTracker._load"):
            from agent.orchestrator import Orchestrator
            orch = Orchestrator()
            orch.execute_feature(
                goal="add docstring to divide",
                codebase_root=str(project),
                pre_planned_tasks=pre_planned,
            )

        modified = src_file.read_text()
        for func_name in ["safe_divide", "factorial", "mean"]:
            orig_func_start = original.index(f"def {func_name}")
            try:
                next_blank = original.index("\n\n", orig_func_start)
            except ValueError:
                next_blank = len(original)
            orig_func_text = original[orig_func_start:next_blank]
            assert orig_func_text in modified, (
                f"AGENT MODIFIED {func_name} WITHOUT PERMISSION.\n"
                f"Diff:\n{_get_diff(original, modified)}"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# Test: Dependency Gate
# ═══════════════════════════════════════════════════════════════════════════════

class TestDependencyGate:
    """Agent must not introduce new dependencies without instruction."""

    def test_no_new_top_level_imports(self, tmp_path):
        project = _make_temp_project(tmp_path)
        src_file = project / "src" / "math_utils.py"
        original_imports = _extract_imports(src_file.read_text())

        mock_result = {
            "success": True,
            "search": 'def mean(values: list) -> float:\n    """Compute arithmetic mean."""\n    if not values:\n        raise ValueError("values must not be empty")\n    return sum(values) / len(values)',
            "replace": 'def mean(values: list) -> float:\n    """Compute arithmetic mean."""\n    if not values:\n        raise ValueError("values must not be empty")\n    return sum(values) / len(values)\n\n\ndef mode(values: list) -> float:\n    """Compute mode (most frequent value)."""\n    if not values:\n        raise ValueError("values must not be empty")\n    from collections import Counter\n    counts = Counter(values)\n    return counts.most_common(1)[0][0]',
            "reasoning": "Added mode function with local Counter import",
        }

        pre_planned = [{
            "task_id": 1, "file": "src/math_utils.py",
            "action": "Add a mode function",
            "complexity": "medium",
            "must_not": ["add new top-level imports", "modify existing functions"],
        }]

        with patch("agent.worker.Worker.execute_task", return_value=mock_result), \
             patch("agent.core.performance_tracker.ToolPerformanceTracker._load"):
            from agent.orchestrator import Orchestrator
            orch = Orchestrator()
            orch.execute_feature(
                goal="add mode function",
                codebase_root=str(project),
                pre_planned_tasks=pre_planned,
            )

        modified = src_file.read_text()
        top_level = set()
        for line in modified.splitlines():
            stripped = line.strip()
            if (stripped.startswith("import ") or stripped.startswith("from ")) \
               and not line.startswith((" ", "\t")):
                for mod in _extract_imports(stripped + "\n"):
                    top_level.add(mod)

        new_top = top_level - original_imports
        assert not new_top, (
            f"AGENT ADDED TOP-LEVEL IMPORTS: {new_top}\n"
            f"Original: {original_imports}\nNew: {top_level}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Test: Negative-Space
# ═══════════════════════════════════════════════════════════════════════════════

class TestNegativeSpace:
    """Test what the agent must NOT do."""

    def test_agent_does_not_refactor_on_bug_fix(self, tmp_path):
        project = _make_temp_project(tmp_path)
        src_file = project / "src" / "math_utils.py"
        original = src_file.read_text()
        orig_lines = len(original.splitlines())

        mock_result = {
            "success": True,
            "search": 'def divide(a: float, b: float) -> float:\n    """Divide a by b."""\n    return a / b',
            "replace": 'def divide(a: float, b: float) -> float:\n    """Divide a by b.\n\n    Raises ZeroDivisionError if b is zero.\n    """\n    if b == 0:\n        raise ZeroDivisionError("division by zero")\n    return a / b',
            "reasoning": "Added explicit zero check",
        }

        pre_planned = [{
            "task_id": 1, "file": "src/math_utils.py",
            "action": "Add explicit ZeroDivisionError to divide function",
            "complexity": "low",
            "must_not": ["modify any other function", "rename anything", "reorganize code"],
        }]

        with patch("agent.worker.Worker.execute_task", return_value=mock_result), \
             patch("agent.core.performance_tracker.ToolPerformanceTracker._load"):
            from agent.orchestrator import Orchestrator
            orch = Orchestrator()
            orch.execute_feature(
                goal="add explicit error to divide",
                codebase_root=str(project),
                pre_planned_tasks=pre_planned,
            )

        modified = src_file.read_text()
        assert abs(len(modified.splitlines()) - orig_lines) <= 5, (
            f"Line count changed too much. Agent may be refactoring:\n"
            f"{_get_diff(original, modified)}"
        )
        for func in ["safe_divide", "factorial", "mean"]:
            assert f"def {func}" in modified, f"AGENT REMOVED {func}"

    def test_bad_change_is_caught_by_test_suite(self, tmp_path):
        """Task: 'Make divide() return a string'. Test suite must catch this."""
        project = _make_temp_project(tmp_path)
        src_file = project / "src" / "math_utils.py"
        original = src_file.read_text()

        mock_result = {
            "success": True,
            "search": 'def divide(a: float, b: float) -> float:\n    """Divide a by b."""\n    return a / b',
            "replace": 'def divide(a: float, b: float) -> str:\n    """Divide a by b, returns string."""\n    return str(a / b)',
            "reasoning": "Changed return type to string",
        }

        pre_planned = [{
            "task_id": 1, "file": "src/math_utils.py",
            "action": "Make divide() return a string instead of float",
            "complexity": "low",
        }]

        with patch("agent.worker.Worker.execute_task", return_value=mock_result), \
             patch("agent.core.performance_tracker.ToolPerformanceTracker._load"):
            from agent.orchestrator import Orchestrator
            orch = Orchestrator()
            orch.execute_feature(
                goal="make divide return string",
                codebase_root=str(project),
                pre_planned_tasks=pre_planned,
            )

        post_pass, post_output = _run_tests(project)
        assert not post_pass, (
            "TEST SUITE IS TOO WEAK: divide() returning str should have "
            "broken tests, but all passed. Add type assertions to tests."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Test: Ambiguity Resistance + Composition
# ═══════════════════════════════════════════════════════════════════════════════

class TestAmbiguityAndComposition:
    """Underspecified tasks + sequential tasks that might conflict."""

    def test_underspecified_task_preserves_invariants(self, tmp_path):
        project = _make_temp_project(tmp_path)
        src_file = project / "src" / "math_utils.py"
        original = src_file.read_text()

        mock_result = {
            "success": True,
            "search": 'def mean(values: list) -> float:\n    """Compute arithmetic mean."""\n    if not values:\n        raise ValueError("values must not be empty")\n    return sum(values) / len(values)',
            "replace": 'def mean(values: list) -> float:\n    """Compute arithmetic mean."""\n    if not isinstance(values, list):\n        raise TypeError("values must be a list")\n    if not values:\n        raise ValueError("values must not be empty")\n    if not all(isinstance(v, (int, float)) for v in values):\n        raise TypeError("all values must be numbers")\n    return sum(values) / len(values)',
            "reasoning": "Added type validation to mean",
        }

        pre_planned = [{
            "task_id": 1, "file": "src/math_utils.py",
            "action": "Add input validation",
            "complexity": "medium",
            "must_not": ["break existing tests", "change return types"],
        }]

        with patch("agent.worker.Worker.execute_task", return_value=mock_result), \
             patch("agent.core.performance_tracker.ToolPerformanceTracker._load"):
            from agent.orchestrator import Orchestrator
            orch = Orchestrator()
            orch.execute_feature(
                goal="add input validation to math_utils",
                codebase_root=str(project),
                pre_planned_tasks=pre_planned,
            )

        post_pass, post_output = _run_tests(project)
        assert post_pass, (
            f"AGENT BROKE TESTS WITH INPUT VALIDATION:\n{post_output}\n\n"
            f"Diff:\n{_get_diff(original, src_file.read_text())}"
        )

    def test_sequential_tasks_dont_conflict(self, tmp_path):
        """Task 1: add clamp. Task 2: add normalize. Must not interfere."""
        project = _make_temp_project(tmp_path)
        src_file = project / "src" / "math_utils.py"
        original = src_file.read_text()

        # Task 1
        mock_1 = {
            "success": True,
            "search": 'def mean(values: list) -> float:\n    """Compute arithmetic mean."""\n    if not values:\n        raise ValueError("values must not be empty")\n    return sum(values) / len(values)',
            "replace": 'def mean(values: list) -> float:\n    """Compute arithmetic mean."""\n    if not values:\n        raise ValueError("values must not be empty")\n    return sum(values) / len(values)\n\n\ndef clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:\n    """Clamp value to [lo, hi]."""\n    return max(lo, min(value, hi))',
            "reasoning": "Added clamp function",
        }

        with patch("agent.worker.Worker.execute_task", return_value=mock_1), \
             patch("agent.core.performance_tracker.ToolPerformanceTracker._load"):
            from agent.orchestrator import Orchestrator
            orch = Orchestrator()
            orch.execute_feature(
                goal="add clamp function",
                codebase_root=str(project),
                pre_planned_tasks=[{
                    "task_id": 1, "file": "src/math_utils.py",
                    "action": "Add a clamp function",
                    "complexity": "low",
                    "must_not": ["modify existing functions"],
                }],
            )

        after_t1 = src_file.read_text()
        assert "def clamp" in after_t1

        # Task 2
        mock_2 = {
            "success": True,
            "search": 'def clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:\n    """Clamp value to [lo, hi]."""\n    return max(lo, min(value, hi))',
            "replace": 'def clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:\n    """Clamp value to [lo, hi]."""\n    return max(lo, min(value, hi))\n\n\ndef normalize(values: list) -> list:\n    """Normalize values to [0, 1] range."""\n    if not values:\n        raise ValueError("values must not be empty")\n    mn, mx = min(values), max(values)\n    if mn == mx:\n        return [0.5] * len(values)\n    return [(v - mn) / (mx - mn) for v in values]',
            "reasoning": "Added normalize function after clamp",
        }

        with patch("agent.worker.Worker.execute_task", return_value=mock_2), \
             patch("agent.core.performance_tracker.ToolPerformanceTracker._load"):
            orch2 = Orchestrator()
            orch2.execute_feature(
                goal="add normalize function",
                codebase_root=str(project),
                pre_planned_tasks=[{
                    "task_id": 2, "file": "src/math_utils.py",
                    "action": "Add a normalize function that scales values to [0,1]",
                    "complexity": "medium",
                    "must_not": ["remove clamp", "modify existing functions"],
                }],
            )

        modified = src_file.read_text()
        assert "def clamp" in modified, "AGENT REMOVED CLAMP WHEN ADDING NORMALIZE"
        assert "def normalize" in modified, "AGENT DID NOT ADD NORMALIZE"

        post_pass, post_output = _run_tests(project)
        assert post_pass, (
            f"SEQUENTIAL TASKS BROKE TESTS:\n{post_output}\n\n"
            f"Diff:\n{_get_diff(original, modified)}"
        )
