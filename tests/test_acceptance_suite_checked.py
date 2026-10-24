"""Prompts claim the visible tests pass only when the suite actually ran."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scaffold.agent import acceptance as acc  # noqa: E402


class _Suite:
    source = "def test_a():\n    assert False\n"
    goal_text = "goal"


def _user(msgs):
    return msgs[-1]["content"]


def test_arbitration_v1_diff_note():
    ran = _user(acc.build_arbitration_messages("g", _Suite(), {"test_a": "boom"}, "d"))
    deferred = _user(acc.build_arbitration_messages("g", _Suite(), {"test_a": "boom"}, "d", False))
    assert "the visible tests pass with it" in ran
    assert "visible tests pass" not in deferred and "has not been run" in deferred


def test_arbitration_v2_diff_note():
    ran = _user(acc.build_arbitration_messages_v2("g", _Suite(), {"test_a": "boom"}, "d"))
    deferred = _user(acc.build_arbitration_messages_v2("g", _Suite(), {"test_a": "boom"}, "d", False))
    assert "the visible tests pass with it" in ran
    assert "visible tests pass" not in deferred and "has not been run" in deferred


def test_repair_feedback_lead():
    assert acc.repair_feedback(_Suite(), {"test_a": "x"}).startswith("The project's visible tests pass")
    deferred = acc.repair_feedback(_Suite(), {"test_a": "x"}, suite_checked=False)
    assert deferred.startswith("Acceptance tests written from the issue")
