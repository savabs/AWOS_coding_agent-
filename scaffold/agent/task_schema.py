"""
task_schema.py — One definition of which files a task touches.

The planner originally emitted one file per task ("Each task must change ONLY
ONE file"), because the single-shot Worker is handed the text of exactly one
file and returns one SEARCH/REPLACE block for it. That constraint excludes the
commonest real change: edit a function, update its callers, fix the test.

Lifting it is additive rather than a rename. Roughly thirty call sites read
`task["file"]`, so that field stays and keeps meaning "the primary file". A
task may now also carry `files`, the full list it touches:

    {"task_id": 1, "file": "auth.py", "files": ["auth.py", "test_auth.py"], ...}

`task_files()` is the single answer to "what does this task touch", so
scheduling, verification and test selection cannot drift apart — which matters
most in DAGExecutor, where missing a shared file means two tasks edit it
concurrently.

This module deliberately imports nothing beyond the standard library: it is
imported by the scheduler, which must not pull in a model SDK.
"""

from __future__ import annotations

from typing import Any, Iterable


def task_files(task: dict[str, Any]) -> list[str]:
    """
    Every file a task touches, primary first, de-duplicated, order preserved.

    Accepts both shapes: a legacy single-file task and a multi-file one. A task
    carrying neither yields an empty list rather than [""], so callers do not
    have to filter a meaningless placeholder.
    """
    ordered: list[str] = []

    primary = task.get("file")
    if isinstance(primary, str) and primary.strip():
        ordered.append(primary.strip())

    extra = task.get("files")
    if isinstance(extra, str):
        extra = [extra]
    if isinstance(extra, Iterable) and not isinstance(extra, (bytes, dict)):
        for path in extra:
            if isinstance(path, str) and path.strip() and path.strip() not in ordered:
                ordered.append(path.strip())

    return ordered


def primary_file(task: dict[str, Any]) -> str:
    """
    The one file a single-file consumer should use.

    Every existing reader of `task["file"]` keeps working; this is for code
    that wants the same answer without assuming the key is present.
    """
    files = task_files(task)
    return files[0] if files else ""


def is_multi_file(task: dict[str, Any]) -> bool:
    return len(task_files(task)) > 1


def normalise_task(task: dict[str, Any]) -> dict[str, Any]:
    """
    Return a copy with `file` and `files` consistent with each other.

    A planner may emit either shape, or a `files` list whose first entry
    disagrees with `file`. Normalising once, at the plan boundary, keeps every
    downstream consumer from having to reconcile them.
    """
    normalised = dict(task)
    files = task_files(task)
    if not files:
        return normalised
    normalised["file"] = files[0]
    if len(files) > 1:
        normalised["files"] = files
    else:
        # A single-file task carries no `files` key, so a plan stays byte-identical
        # to what the planner produced before multi-file support existed.
        normalised.pop("files", None)
    return normalised


def describe_files(task: dict[str, Any]) -> str:
    """Short human-readable rendering of a task's files, for logs and prompts."""
    files = task_files(task)
    if not files:
        return "(no file)"
    if len(files) == 1:
        return files[0]
    return f"{files[0]} (+{len(files) - 1} more: {', '.join(files[1:])})"
