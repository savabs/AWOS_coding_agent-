"""
goal_acceptance.py — Cheap post-run checks that deliverables exist and look valid.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


def verify_deliverable(path: str | Path, min_bytes: int = 32) -> list[str]:
    """Return list of errors; empty means accepted."""
    p = Path(path)
    errors: list[str] = []

    if not p.exists():
        errors.append(f"Deliverable missing: {p}")
        return errors

    if not p.is_file():
        errors.append(f"Not a file: {p}")
        return errors

    raw = p.read_bytes()
    if len(raw) < min_bytes:
        errors.append(f"Deliverable too small ({len(raw)} bytes): {p}")

    text = raw.decode("utf-8", errors="replace")
    ext = p.suffix.lower()

    if ext == ".py":
        try:
            compile(text, str(p), "exec")
        except SyntaxError as e:
            errors.append(f"Python syntax error in {p}: {e.msg} (line {e.lineno})")
    elif ext == ".json":
        try:
            json.loads(text)
        except json.JSONDecodeError as e:
            errors.append(f"Invalid JSON in {p}: {e.msg}")
    elif ext in (".html", ".htm"):
        if "<" not in text or ">" not in text:
            errors.append(f"Does not look like HTML: {p}")
    elif ext in (".md", ".rst", ".txt"):
        if len(text.strip()) < 20:
            errors.append(f"Document content too short: {p}")

    return errors


def acceptance_for_create_goal(goal: str, paths: list[str], codebase_root: str | Path) -> tuple[bool, list[str]]:
    """Check all created paths; goal may mention explicit path."""
    root = Path(codebase_root)
    all_errors: list[str] = []

    if not paths:
        all_errors.append("No deliverable paths recorded")
        return False, all_errors

    for rel in paths:
        full = root / rel
        all_errors.extend(verify_deliverable(full))

    # Goal mentioned a specific file — ensure it was created
    m = re.search(
        r"[`\"']?((?:[\w.-]+/)*[\w.-]+\.(?:md|html|htm|json|yaml|yml|txt|csv|xml|rst))[`\"']?",
        goal,
        re.IGNORECASE,
    )
    if m:
        wanted = m.group(1).lstrip("./")
        if wanted not in paths and not (root / wanted).exists():
            all_errors.append(f"Goal referenced {wanted} but it was not created")

    return len(all_errors) == 0, all_errors
