"""Tests for planner-owned task types (create_file / edit_file)."""

from pathlib import Path

from scaffold.agent.plan_actions import (
    CREATE_FILE,
    EDIT_FILE,
    extract_path_from_goal,
    infer_task_type,
    is_create_file,
    normalize_plan,
    normalize_task,
)


def test_extract_explicit_path():
    assert extract_path_from_goal("write docs/foo.md please") == "docs/foo.md"


def test_create_file_task_type():
    task = normalize_task({
        "task_id": 1,
        "task_type": CREATE_FILE,
        "path": "docs/new.md",
        "complexity": "low",
    })
    assert is_create_file(task)
    assert task["file"] == "docs/new.md"


def test_infer_edit_when_file_exists(tmp_path):
    f = tmp_path / "existing.py"
    f.write_text("x = 1\n")
    task = normalize_task({
        "task_id": 1,
        "path": "existing.py",
        "complexity": "low",
    }, tmp_path)
    assert task["task_type"] == EDIT_FILE


def test_infer_create_when_missing(tmp_path):
    task = normalize_task({
        "task_id": 1,
        "path": "docs/new.md",
        "complexity": "low",
    }, tmp_path)
    assert task["task_type"] == CREATE_FILE


def test_normalize_plan_batch(tmp_path):
    tasks = normalize_plan([
        {"task_id": 1, "task_type": CREATE_FILE, "path": "a.md", "complexity": "low"},
        {"task_id": 2, "task_type": EDIT_FILE, "path": "b.py", "complexity": "low"},
    ], tmp_path)
    assert len(tasks) == 2
    assert tasks[0]["file"] == "a.md"
