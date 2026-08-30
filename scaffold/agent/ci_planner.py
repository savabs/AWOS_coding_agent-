"""
CI Planner — Generate CI repair goals and task plans from test failures.

Generates CI-specific goal strings and deterministic task plans from
discovered test failures. No LLM calls — fast, cheap, predictable.

Cost: FREE (no API calls).
"""

from __future__ import annotations

import logging
from typing import List

logger = logging.getLogger(__name__)


def generate_ci_goal(failures: List[dict]) -> str:
    """
    Generate a CI repair goal from a list of test failures.

    Args:
        failures: List of failure dicts from ci_discovery.discover_ci_failures()
            Each dict has: module, test_name, test_file, error_msg, error_type

    Returns:
        Goal string for the orchestrator.

    Example:
        "Fix 12 failing tests in auth.py, billing.py, inventory.py.
         Do not edit tests/. Keep 7 regression tests green."
    """
    if not failures:
        return "No failing tests found. CI is already green."

    # Get unique modules
    modules = sorted(set(f["module"] for f in failures))
    module_list = ", ".join(modules)

    # Count failures
    n_failures = len(failures)

    # Build goal
    goal = (
        f"CI repair: Fix {n_failures} failing tests in {module_list}. "
        f"Do not edit tests/ or conftest.py. "
        f"Keep all currently-passing regression tests green. "
        f"Fix the source code to make the tests pass."
    )

    return goal


def generate_ci_plan(failures: List[dict]) -> List[dict]:
    """
    Generate a deterministic task plan from test failures.

    Creates one task per failure, grouped by module. Each task describes
    what to fix in the source module based on the test failure.

    Args:
        failures: List of failure dicts from ci_discovery.discover_ci_failures()

    Returns:
        List of task dicts matching orchestrator format:
        [
            {
                "task_id": 1,
                "task_type": "edit_file",
                "file": "auth.py",
                "path": "auth.py",
                "action": "Fix test_login: AssertionError at line 45",
                "complexity": "low",
            },
            ...
        ]
    """
    if not failures:
        return []

    # Group failures by module
    by_module: dict[str, list] = {}
    for f in failures:
        module = f["module"]
        if module not in by_module:
            by_module[module] = []
        by_module[module].append(f)

    # Generate tasks
    tasks = []
    task_id = 1

    for module in sorted(by_module.keys()):
        module_failures = by_module[module]

        for failure in module_failures:
            test_name = failure["test_name"]
            error_msg = failure.get("error_msg", "")
            error_type = failure.get("error_type", "")
            line_number = failure.get("line_number")

            # Build action description
            if error_type and error_msg:
                # Truncate error message for readability
                error_short = error_msg[:80] if len(error_msg) > 80 else error_msg
                action = f"Fix {test_name}: {error_type} — {error_short}"
            elif error_msg:
                error_short = error_msg[:80] if len(error_msg) > 80 else error_msg
                action = f"Fix {test_name}: {error_short}"
            else:
                action = f"Fix {test_name}"

            # Add line number if available
            if line_number:
                action += f" (line {line_number})"

            # Determine complexity
            complexity = _estimate_complexity(failure)

            tasks.append({
                "task_id": task_id,
                "task_type": "edit_file",
                "file": module,
                "path": module,
                "action": action,
                "complexity": complexity,
            })
            task_id += 1

    return tasks


def _estimate_complexity(failure: dict) -> str:
    """
    Estimate task complexity from failure characteristics.

    Heuristics:
    - AssertionError → low (usually a simple logic fix)
    - TypeError → medium (may need type changes)
    - ImportError → low (missing import)
    - Multiple errors in same module → medium
    - Integration test → high
    """
    error_type = failure.get("error_type", "")
    test_name = failure.get("test_name", "")
    test_file = failure.get("test_file", "")

    # Integration tests are usually more complex
    if "integration" in test_file.lower() or "integration" in test_name.lower():
        return "high"

    # TypeError often needs more thought
    if "TypeError" in error_type:
        return "medium"

    # AttributeError may need more context
    if "AttributeError" in error_type:
        return "medium"

    # AssertionError is usually a simple logic fix
    if "AssertionError" in error_type or "AssertionError" in error_type:
        return "low"

    # Default to low
    return "low"


def generate_ci_plan_grouped(failures: List[dict]) -> List[dict]:
    """
    Generate a grouped task plan — one task per module (not per failure).

    Useful when there are many failures in the same module and we want
    the worker to fix them all in one pass.

    Args:
        failures: List of failure dicts

    Returns:
        List of task dicts (one per module):
        [
            {
                "task_id": 1,
                "file": "auth.py",
                "action": "Fix 4 failing tests: test_login, test_session, ...",
                "complexity": "medium",
            },
            ...
        ]
    """
    if not failures:
        return []

    # Group by module
    by_module: dict[str, list] = {}
    for f in failures:
        module = f["module"]
        if module not in by_module:
            by_module[module] = []
        by_module[module].append(f)

    tasks = []
    task_id = 1

    for module in sorted(by_module.keys()):
        module_failures = by_module[module]
        n_failures = len(module_failures)

        # List test names
        test_names = [f["test_name"] for f in module_failures]
        test_list = ", ".join(test_names[:5])  # Show first 5
        if len(test_names) > 5:
            test_list += f", ... ({n_failures} total)"

        action = f"Fix {n_failures} failing tests: {test_list}"

        # Complexity based on number of failures
        if n_failures >= 5:
            complexity = "high"
        elif n_failures >= 3:
            complexity = "medium"
        else:
            complexity = "low"

        tasks.append({
            "task_id": task_id,
            "task_type": "edit_file",
            "file": module,
            "path": module,
            "action": action,
            "complexity": complexity,
        })
        task_id += 1

    return tasks
