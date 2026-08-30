"""
Tests for CI Planner module.

Tests goal generation, plan generation, and grouping.
"""

import pytest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "scaffold" / "agent"))

from ci_planner import generate_ci_goal, generate_ci_plan, generate_ci_plan_grouped


# ── Fixtures ──────────────────────────────────────────────────────────────

SAMPLE_FAILURES = [
    {
        "module": "auth.py",
        "test_name": "test_login",
        "test_file": "tests/test_auth.py",
        "error_msg": "AssertionError: expected True, got False",
        "error_type": "AssertionError",
        "line_number": 45,
    },
    {
        "module": "auth.py",
        "test_name": "test_session",
        "test_file": "tests/test_auth.py",
        "error_msg": "TypeError: expected str, got int",
        "error_type": "TypeError",
        "line_number": 67,
    },
    {
        "module": "billing.py",
        "test_name": "test_invoice",
        "test_file": "tests/test_billing.py",
        "error_msg": "AssertionError: 100 != 99",
        "error_type": "AssertionError",
        "line_number": 23,
    },
]


# ── Tests ─────────────────────────────────────────────────────────────────

class TestGenerateCIGoal:
    """Test CI goal generation."""

    def test_goal_includes_failure_count(self):
        """Goal should include number of failing tests."""
        goal = generate_ci_goal(SAMPLE_FAILURES)
        assert "3 failing tests" in goal

    def test_goal_includes_modules(self):
        """Goal should list affected modules."""
        goal = generate_ci_goal(SAMPLE_FAILURES)
        assert "auth.py" in goal
        assert "billing.py" in goal

    def test_goal_includes_constraints(self):
        """Goal should include 'do not edit tests' constraint."""
        goal = generate_ci_goal(SAMPLE_FAILURES)
        assert "Do not edit tests/" in goal

    def test_goal_empty_failures(self):
        """Empty failure list should return 'no failures' message."""
        goal = generate_ci_goal([])
        assert "No failing tests" in goal or "green" in goal.lower()


class TestGenerateCIPlan:
    """Test CI plan generation (per-failure)."""

    def test_plan_has_one_task_per_failure(self):
        """Should generate one task per failure."""
        plan = generate_ci_plan(SAMPLE_FAILURES)
        assert len(plan) == 3

    def test_plan_task_format(self):
        """Each task should have required fields."""
        plan = generate_ci_plan(SAMPLE_FAILURES)
        for task in plan:
            assert "task_id" in task
            assert "file" in task
            assert "action" in task
            assert "complexity" in task
            assert task["complexity"] in ("low", "medium", "high")

    def test_plan_task_ids_sequential(self):
        """Task IDs should be sequential starting from 1."""
        plan = generate_ci_plan(SAMPLE_FAILURES)
        ids = [t["task_id"] for t in plan]
        assert ids == [1, 2, 3]

    def test_plan_groups_by_module(self):
        """Tasks should be grouped by module (auth first, then billing)."""
        plan = generate_ci_plan(SAMPLE_FAILURES)
        files = [t["file"] for t in plan]
        # auth.py tasks should come before billing.py
        auth_indices = [i for i, f in enumerate(files) if f == "auth.py"]
        billing_indices = [i for i, f in enumerate(files) if f == "billing.py"]
        assert max(auth_indices) < min(billing_indices)

    def test_plan_empty_failures(self):
        """Empty failure list should return empty plan."""
        plan = generate_ci_plan([])
        assert plan == []

    def test_plan_includes_error_info(self):
        """Task action should include error type and message."""
        plan = generate_ci_plan(SAMPLE_FAILURES)
        # First task should mention the test name
        assert "test_login" in plan[0]["action"]


class TestGenerateCIPlanGrouped:
    """Test grouped CI plan generation (one task per module)."""

    def test_grouped_plan_one_per_module(self):
        """Should generate one task per module."""
        plan = generate_ci_plan_grouped(SAMPLE_FAILURES)
        assert len(plan) == 2  # auth.py and billing.py

    def test_grouped_plan_lists_tests(self):
        """Grouped task action should list test names."""
        plan = generate_ci_plan_grouped(SAMPLE_FAILURES)
        auth_task = [t for t in plan if t["file"] == "auth.py"][0]
        assert "test_login" in auth_task["action"]
        assert "test_session" in auth_task["action"]

    def test_grouped_plan_shows_count(self):
        """Grouped task action should show failure count."""
        plan = generate_ci_plan_grouped(SAMPLE_FAILURES)
        auth_task = [t for t in plan if t["file"] == "auth.py"][0]
        assert "2 failing tests" in auth_task["action"]

    def test_grouped_plan_empty(self):
        """Empty failure list should return empty plan."""
        plan = generate_ci_plan_grouped([])
        assert plan == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
