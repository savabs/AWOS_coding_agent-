"""
diff_builder.py — Structured file diffs for GUI rendering.

Produces machine-readable change records (hunks, line numbers, add/remove counts)
from search/replace pairs or git diff output.
"""

from __future__ import annotations

import difflib
import re
import subprocess
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class FileStatus(str, Enum):
    MODIFIED = "modified"
    CREATED = "created"
    DELETED = "deleted"
    RENAMED = "renamed"


@dataclass
class DiffLine:
    type: str  # add | remove | context | header
    content: str
    old_line_no: Optional[int] = None
    new_line_no: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DiffHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[DiffLine] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["lines"] = [ln.to_dict() for ln in self.lines]
        return d


@dataclass
class FileChange:
    path: str
    status: str
    lines_added: int = 0
    lines_removed: int = 0
    hunks: list[DiffHunk] = field(default_factory=list)
    unified_diff: str = ""
    before_excerpt: str = ""
    after_excerpt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "status": self.status,
            "lines_added": self.lines_added,
            "lines_removed": self.lines_removed,
            "hunks": [h.to_dict() for h in self.hunks],
            "unified_diff": self.unified_diff,
            "before_excerpt": self.before_excerpt,
            "after_excerpt": self.after_excerpt,
        }


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _detect_status(
    file_path: str,
    search: str,
    replace: str,
    *,
    codebase_root: str = ".",
) -> str:
    full = Path(codebase_root) / file_path
    existed = full.exists()
    if not existed and replace:
        return FileStatus.CREATED.value
    if existed and not replace and search:
        return FileStatus.DELETED.value
    if not search and replace and not existed:
        return FileStatus.CREATED.value
    return FileStatus.MODIFIED.value


def _parse_unified_diff(lines: list[str]) -> tuple[list[DiffHunk], int, int]:
    hunks: list[DiffHunk] = []
    current: Optional[DiffHunk] = None
    old_ln = 0
    new_ln = 0
    added = 0
    removed = 0

    for raw in lines:
        line = raw.rstrip("\n")
        m = _HUNK_RE.match(line)
        if m:
            if current and current.lines:
                hunks.append(current)
            old_start = int(m.group(1))
            new_start = int(m.group(3))
            current = DiffHunk(
                old_start=old_start,
                old_count=int(m.group(2) or 1),
                new_start=new_start,
                new_count=int(m.group(4) or 1),
            )
            old_ln = old_start
            new_ln = new_start
            current.lines.append(DiffLine(type="header", content=line))
            continue

        if current is None:
            if line.startswith("+++") or line.startswith("---"):
                continue
            continue

        if line.startswith("+") and not line.startswith("+++"):
            current.lines.append(
                DiffLine(type="add", content=line[1:], new_line_no=new_ln)
            )
            new_ln += 1
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            current.lines.append(
                DiffLine(type="remove", content=line[1:], old_line_no=old_ln)
            )
            old_ln += 1
            removed += 1
        elif line.startswith(" "):
            current.lines.append(
                DiffLine(
                    type="context",
                    content=line[1:],
                    old_line_no=old_ln,
                    new_line_no=new_ln,
                )
            )
            old_ln += 1
            new_ln += 1

    if current and current.lines:
        hunks.append(current)

    return hunks, added, removed


def _git_diff_lines(file_path: str, codebase_root: str) -> list[str]:
    try:
        proc = subprocess.run(
            ["git", "diff", "--no-color", "--", file_path],
            cwd=codebase_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return proc.stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return []


def _git_untracked_status(file_path: str, codebase_root: str) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain", "--", file_path],
            cwd=codebase_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        line = proc.stdout.strip()
        if not line:
            return None
        code = line[:2]
        if code == "??":
            return FileStatus.CREATED.value
        if code.strip() == "D":
            return FileStatus.DELETED.value
        return FileStatus.MODIFIED.value
    except (OSError, subprocess.SubprocessError):
        return None


def build_file_change(
    file_path: str,
    search: str = "",
    replace: str = "",
    *,
    codebase_root: str = ".",
) -> FileChange:
    """Build a structured FileChange from search/replace or git diff."""
    status = _detect_status(file_path, search, replace, codebase_root=codebase_root)
    git_status = _git_untracked_status(file_path, codebase_root)
    if git_status:
        status = git_status

    unified_lines: list[str] = []
    if search or replace:
        a = search.splitlines(keepends=True) or ([search + "\n"] if search else [])
        b = replace.splitlines(keepends=True) or ([replace + "\n"] if replace else [])
        unified_lines = list(
            difflib.unified_diff(
                a,
                b,
                fromfile=f"{file_path} (before)",
                tofile=f"{file_path} (after)",
                lineterm="",
            )
        )
    else:
        unified_lines = _git_diff_lines(file_path, codebase_root)

    hunks, added, removed = _parse_unified_diff(unified_lines)
    before_excerpt = search[:2000] if search else ""
    after_excerpt = replace[:2000] if replace else ""

    return FileChange(
        path=file_path,
        status=status,
        lines_added=added,
        lines_removed=removed,
        hunks=hunks,
        unified_diff="\n".join(unified_lines),
        before_excerpt=before_excerpt,
        after_excerpt=after_excerpt,
    )


def collect_session_files(changes: list[FileChange]) -> dict[str, Any]:
    """Aggregate file-change list for GUI file tree / summary panel."""
    by_status: dict[str, list[str]] = {
        FileStatus.CREATED.value: [],
        FileStatus.MODIFIED.value: [],
        FileStatus.DELETED.value: [],
        FileStatus.RENAMED.value: [],
    }
    total_added = 0
    total_removed = 0
    for ch in changes:
        by_status.setdefault(ch.status, []).append(ch.path)
        total_added += ch.lines_added
        total_removed += ch.lines_removed

    return {
        "files": [c.to_dict() for c in changes],
        "summary": {
            "total_files": len(changes),
            "created": len(by_status[FileStatus.CREATED.value]),
            "modified": len(by_status[FileStatus.MODIFIED.value]),
            "deleted": len(by_status[FileStatus.DELETED.value]),
            "lines_added": total_added,
            "lines_removed": total_removed,
        },
        "by_status": by_status,
    }
