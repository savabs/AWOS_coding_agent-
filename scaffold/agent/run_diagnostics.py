"""
run_diagnostics.py — Per-run stage tracing for self-validation on AWOS repo.

Surfaces where time, tokens, and failures occur: plan → route → worker → verify → learn.
"""

from __future__ import annotations

import difflib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class TaskDiagnostic:
    task_id: str
    goal: str = ""
    file: str = ""
    stages: list[dict[str, Any]] = field(default_factory=list)
    success: bool = False
    failure_stage: str = ""
    failure_reason: str = ""

    def record(self, stage: str, **kwargs: Any) -> None:
        """Append a stage entry to the diagnostic trace."""
        self.stages.append({"stage": stage, **kwargs})

    def mark_failed(self, stage: str, reason: str) -> None:
        self.failure_stage = stage
        self.failure_reason = reason
        self.success = False

    def summary_lines(self) -> list[str]:
        lines = [f"  Task {self.task_id}  {self.file or '?'}  →  {'OK' if self.success else 'FAIL'}"]
        for s in self.stages:
            name = s.get("stage", "?")
            bits = []
            for k in ("model", "reason", "tokens", "cost_usd", "tier", "detail", "ms"):
                if k in s and s[k] not in (None, "", 0):
                    v = s[k]
                    if k == "cost_usd":
                        bits.append(f"${float(v):.6f}")
                    elif k == "tokens":
                        bits.append(f"{int(v)} tok")
                    elif k == "ms":
                        bits.append(f"{int(v)}ms")
                    else:
                        bits.append(f"{k}={v}")
            extra = " · ".join(bits)
            mark = "✓" if s.get("ok", True) else "✗"
            lines.append(f"    {mark} {name:<12} {extra}")
        if not self.success and self.failure_stage:
            lines.append(f"    ✗ failed at {self.failure_stage}: {self.failure_reason[:120]}")
        return lines


class RunDiagnostics:
    """Accumulates per-task diagnostics for one `awos run` session."""

    def __init__(self, goal: str = "", cheap_only: bool = False):
        self.goal = goal
        self.cheap_only = cheap_only
        self.tasks: list[TaskDiagnostic] = []
        self._current: Optional[TaskDiagnostic] = None
        self.session_notes: list[str] = []

    def start_task(self, task_id: str, task: dict) -> TaskDiagnostic:
        td = TaskDiagnostic(
            task_id=str(task_id),
            goal=self.goal,
            file=task.get("file", ""),
        )
        self.tasks.append(td)
        self._current = td
        return td

    @property
    def current(self) -> Optional[TaskDiagnostic]:
        return self._current

    def note(self, msg: str) -> None:
        self.session_notes.append(msg)

    def print_task_summary(self, td: TaskDiagnostic) -> None:
        print("\n┌─ RUN DIAGNOSTICS (this task) ─────────────────────────")
        for line in td.summary_lines():
            print(f"│{line}")
        print("└────────────────────────────────────────────────────────\n")

    def print_session_summary(
        self,
        *,
        total_cost: float = 0.0,
        completed: int = 0,
        failed: int = 0,
    ) -> None:
        n = len(self.tasks)
        ok = sum(1 for t in self.tasks if t.success)
        print("\n┌─ RUN DIAGNOSTICS (session) ────────────────────────────")
        print(f"│  Goal: {self.goal[:55]}{'…' if len(self.goal) > 55 else ''}")
        print(f"│  Mode: {'cheap-only' if self.cheap_only else 'standard'}")
        print(f"│  Tasks: {ok}/{n} diagnostic-ok · run {completed}/{completed + failed} completed")
        print(f"│  Session cost (orchestrator): ${total_cost:.6f}")
        # Failure histogram
        by_stage: dict[str, int] = {}
        for t in self.tasks:
            if not t.success and t.failure_stage:
                by_stage[t.failure_stage] = by_stage.get(t.failure_stage, 0) + 1
        if by_stage:
            print("│  Failures by stage:")
            for stage, count in sorted(by_stage.items(), key=lambda x: -x[1]):
                print(f"│    {stage}: {count}")
        for note in self.session_notes[-5:]:
            print(f"│  · {note[:60]}")
        print("└────────────────────────────────────────────────────────\n")


# Shows the applied patch in the terminal.
def print_visual_diff(
    file_path: str,
    search: str = "",
    replace: str = "",
    *,
    codebase_root: str = ".",
    max_lines: int = 16,
) -> Optional[dict]:
    """Print a human-readable diff; return structured change dict for GUI."""
    try:
        from .diff_builder import build_file_change
    except ImportError:
        from diff_builder import build_file_change

    change = build_file_change(
        file_path,
        search,
        replace,
        codebase_root=codebase_root,
    )
    lines = change.unified_diff.splitlines() if change.unified_diff else []
    if not lines:
        return None

    print("\n┌─ APPLIED CHANGE (visual) ───────────────────────────────")
    status_label = change.status.upper()
    print(f"│  File: {file_path}  [{status_label}]  +{change.lines_added} -{change.lines_removed}")
    shown = 0
    for line in lines:
        if shown >= max_lines:
            print(f"│  … ({len(lines) - shown} more diff lines)")
            break
        if line.startswith("+") and not line.startswith("+++"):
            prefix = "│  +"
        elif line.startswith("-") and not line.startswith("---"):
            prefix = "│  -"
        else:
            prefix = "│   "
        print(f"{prefix}{line.rstrip()[:90]}")
        shown += 1
    print("└────────────────────────────────────────────────────────\n")
    print(f"  → Review: git diff {file_path}")
    print(f"  → Open:   {Path(codebase_root, file_path)}\n")
    return change.to_dict()
