"""TaskDecomposer heuristic — the failure-retry splitter."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scaffold.agent.task_decomposer import TaskDecomposer


def _actions(task):
    return [t["action"] for t in TaskDecomposer().decompose(task)]


def test_conjunctions_are_not_returned_as_tasks():
    # re.split with a capturing group once produced a task named "and".
    parts = _actions({"task_id": 2, "file": "a.py",
                      "action": "Review the pagination tests and update test assertions for page sizes"})
    assert "and" not in [p.lower() for p in parts]
    assert all(len(p.split()) >= 3 for p in parts)


def test_no_genuine_split_declines_instead_of_rewording():
    # "Prepare X" / "Complete X" is the same task twice; it only repeats the failure.
    assert _actions({"task_id": 1, "file": "a.py", "action": "Fix the slice in paginate"}) == []


def test_real_multi_step_task_still_splits():
    parts = _actions({"task_id": 1, "file": "a.py",
                      "action": "Create the auth module with a login function and then wire it into the main app"})
    assert len(parts) == 2
    assert parts[0].startswith("Create the auth module")
