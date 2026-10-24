"""
plan_actions.py — Structured planner task types and normalization.

Runtime understands coarse actions only:
  create_file | edit_file

Path and content come from the planner (LLM), not Python heuristics.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

CREATE_FILE = "create_file"
EDIT_FILE = "edit_file"
VALID_TASK_TYPES = frozenset({CREATE_FILE, EDIT_FILE})

_PATH_IN_TEXT = re.compile(
    r"""[`"']?((?:[\w.-]+/)*[\w.-]+\.(?:md|html|htm|json|yaml|yml|txt|csv|xml|rst|py|toml))[`"']?""",
    re.IGNORECASE,
)


def extract_path_from_goal(goal: str) -> str | None:
    """Pull an explicit relative path from goal text (no convention guessing)."""
    m = _PATH_IN_TEXT.search(goal or "")
    return m.group(1).lstrip("./") if m else None


def task_path(task: dict[str, Any]) -> str:
    return (task.get("path") or task.get("file") or "").strip()


def is_create_file(task: dict[str, Any]) -> bool:
    return (task.get("task_type") or "").strip() == CREATE_FILE


def is_edit_file(task: dict[str, Any]) -> bool:
    tt = (task.get("task_type") or "").strip()
    return tt == EDIT_FILE or not tt


def infer_task_type(task: dict[str, Any], codebase_root: str | Path = ".") -> str:
    """Fallback when planner omits task_type (legacy plans)."""
    explicit = (task.get("task_type") or "").strip()
    if explicit in VALID_TASK_TYPES:
        return explicit

    path = task_path(task)
    if not path:
        return EDIT_FILE

    full = Path(codebase_root) / path
    if full.exists() and full.is_file():
        return EDIT_FILE
    return CREATE_FILE


def normalize_task(task: dict[str, Any], codebase_root: str | Path = ".") -> dict[str, Any]:
    """Ensure task has task_type, path, and file (alias)."""
    out = dict(task)
    path = task_path(out)
    if path:
        out["path"] = path
        out["file"] = path

    out["task_type"] = infer_task_type(out, codebase_root)
    return out


def normalize_plan(tasks: list[dict[str, Any]], codebase_root: str | Path = ".") -> list[dict[str, Any]]:
    return [normalize_task(t, codebase_root) for t in tasks]


def validate_plan_task(task: dict[str, Any]) -> None:
    required = ["task_id", "complexity"]
    if not all(k in task for k in required):
        raise ValueError(f"Task missing required fields: {task}")
    if not task_path(task):
        raise ValueError(f"Task missing path/file: {task}")
    tt = infer_task_type(task)
    if tt not in VALID_TASK_TYPES:
        raise ValueError(f"Invalid task_type: {tt}")


def replan_task_after_verify_fail(
    failed_task: dict[str, Any],
    verify_error: str,
) -> list[dict[str, Any]]:
    """Build one revised task when verification rejected the worker patch."""
    if failed_task.get("_replan_attempted"):
        return []

    base_id = failed_task.get("task_id", "task")
    err = (verify_error or "").lower()
    action = str(failed_task.get("action", "")).rstrip(".")

    if "comment above" in err or "fidelity" in err:
        hint = (
            " Use a # hash comment on the line immediately ABOVE the target symbol, "
            "NOT a docstring inside the function body."
        )
    elif "search" in err and "not found" in err:
        hint = (
            " Copy SEARCH text verbatim from the file — anchor on the function signature line."
        )
    elif "syntax" in err:
        hint = " Minimal fix: correct indentation/syntax only; change one line if possible."
    else:
        hint = f" Previous verify error: {verify_error[:200]}. Use a simpler, more literal edit."

    revised = dict(failed_task)
    revised["task_id"] = f"{base_id}_r1"
    revised["action"] = f"{action}.{hint}"
    revised["complexity"] = "low"
    revised["_replan_attempted"] = True
    revised["parent_task_id"] = base_id
    return [revised]

PLANNER_ACTION_SCHEMA = """
TASK TYPES (task_type field — required):
- create_file  → new file; runtime calls WriteFileTool (any extension)
- edit_file    → patch existing file; runtime uses SEARCH/REPLACE

Each task:
{
  "task_id": 1,
  "task_type": "create_file|edit_file",
  "path": "relative/path/from/repo/root",
  "action": "one-line description of what to do",
  "content_hint": "for create_file only — topics/sections to include",
  "complexity": "low|medium|high"
}

RULES:
1. YOU decide the path using PROJECT LAYOUT + PLACEMENT CONVENTIONS below — never dump new artifacts at repo root.
2. create_file when the deliverable does not exist yet; edit_file when modifying existing code/docs.
3. Match neighbor examples (docs/research/, tasks/active/, etc.).
4. One file per task. Order dependencies first.
5. For edit_file on Python code, optional contract fields: function_signature, constraints, must_not.
"""
