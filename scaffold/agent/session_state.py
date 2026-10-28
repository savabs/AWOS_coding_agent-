"""
Session State — P1.3 Auto Context Compaction.

Tracks completed tasks during a multi-task feature execution and provides
a compact context block injected into each worker prompt. Prevents token
bloat on long features (5+ tasks) by summarizing old tasks when context
exceeds 800 tokens.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Optional, Callable

MAX_CONTEXT_TOKENS = 800   # Compact when estimated tokens exceed this


@dataclass
class TaskSummary:
    """Compact record of one completed task — fits in 2-3 prompt lines."""
    task_id: int
    action: str
    file: str
    status: str              # "completed" | "failed" | "skipped"
    new_symbols: list[str]   # New function/class names added
    key_change: str          # One-line description of what changed
    test_result: Optional[str] = None   # "passed" | "failed" | None


@dataclass
class SessionState:
    """
    Tracks the state of an in-progress feature execution.
    Injected into each task prompt via to_context_block().
    Auto-compacts with a cheap LLM call when context grows too large.
    """
    feature_goal: str
    total_tasks: int
    completed_tasks: list[TaskSummary] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    current_task_index: int = 0
    compact_summary: str = ""    # Populated when compaction triggers

    # ── Public API ────────────────────────────────────────────────────────

    def record_task(self, task: dict, result: dict, file_content_before: str = "") -> None:
        """
        Update state after a task executes.

        Args:
            task:   The task dict (task_id, action, file, ...)
            result: The dict returned by _execute_single_task (success, search, replace, ...)
            file_content_before: File content before patch (for symbol diff)
        """
        success = result.get("success", False)
        file_path = task.get("file", "")

        # Extract newly added symbols
        new_syms: list[str] = []
        if success and file_content_before and result.get("replace"):
            new_syms = _extract_new_symbols(file_content_before, result["replace"])

        # Determine test status
        tr = task.get("test_result")
        test_label: Optional[str] = None
        if tr is not None:
            if getattr(tr, "no_tests_found", True):
                test_label = None
            elif getattr(tr, "pass_rate", 0.0) >= 1.0:
                test_label = "passed"
            else:
                test_label = "failed"

        summary = TaskSummary(
            task_id=task.get("task_id", self.current_task_index),
            action=task.get("action", "")[:120],
            file=file_path,
            status="completed" if success else "failed",
            new_symbols=new_syms[:6],   # cap at 6 to save tokens
            key_change=result.get("reasoning", task.get("action", ""))[:100],
            test_result=test_label,
        )
        self.completed_tasks.append(summary)

        if success and file_path and file_path not in self.modified_files:
            self.modified_files.append(file_path)

    def to_context_block(self) -> str:
        """
        Generate the compact context string injected into each task prompt.
        Returns "" when no tasks have completed yet (first task needs no context).
        """
        if not self.completed_tasks and not self.compact_summary:
            return ""

        lines = [
            f"## Session Context — Feature: {self.feature_goal[:80]}",
            f"Progress: {self.current_task_index}/{self.total_tasks}",
            "",
        ]

        if self.compact_summary:
            lines.append("### Previous work (summarized):")
            lines.append(self.compact_summary)
            lines.append("")
        else:
            lines.append("### Completed Tasks:")
            for t in self.completed_tasks:
                icon = "✅" if t.status == "completed" else "❌"
                lines.append(f"{icon} Task {t.task_id}: {t.action}")
                lines.append(f"   File: {t.file} | {t.key_change}")
                if t.new_symbols:
                    lines.append(f"   New symbols: {', '.join(t.new_symbols)}")
                if t.test_result:
                    lines.append(f"   Tests: {t.test_result}")
                lines.append("")

        if self.modified_files:
            lines.append(f"### Modified files so far: {', '.join(self.modified_files)}")

        return "\n".join(lines)

    def maybe_compact(
        self,
        cheap_llm_caller: Optional[Callable[[str, int], str]] = None,
        threshold: int = MAX_CONTEXT_TOKENS,
    ) -> None:
        """
        If context exceeds threshold tokens, compress to a compact_summary.
        Pass a callable cheap_llm_caller(prompt, max_tokens) -> str to enable LLM compression.
        Falls back to a structured text summary if no LLM is available.
        threshold: override MAX_CONTEXT_TOKENS (useful in tests).
        """
        context = self.to_context_block()
        estimated_tokens = len(context.split()) * 1.3

        if estimated_tokens <= threshold:
            return   # Nothing to do

        if cheap_llm_caller is not None:
            prompt = (
                "Summarize these completed coding tasks in 3-5 bullet points.\n"
                "Focus on: what was added, which files were modified, key new symbols created.\n"
                "Be very concise — this will be shown to an LLM for context.\n\n"
                f"Tasks completed:\n{context}\n\n"
                "Summary (3-5 bullet points, max 200 words):"
            )
            try:
                self.compact_summary = cheap_llm_caller(prompt, 250).strip()
            except Exception:
                self.compact_summary = self._fallback_compact()
        else:
            self.compact_summary = self._fallback_compact()

        self.completed_tasks = []   # Clear detailed list — compact_summary now holds it

    def _fallback_compact(self) -> str:
        """Build a structured compact summary without LLM."""
        if not self.completed_tasks:
            return ""
        ok = [t for t in self.completed_tasks if t.status == "completed"]
        fail = [t for t in self.completed_tasks if t.status != "completed"]
        syms = [s for t in ok for s in t.new_symbols]
        lines = [f"• Completed {len(ok)} tasks across {len(self.modified_files)} file(s)."]
        if syms:
            lines.append(f"• New symbols added: {', '.join(syms[:8])}.")
        if fail:
            lines.append(f"• {len(fail)} tasks failed: {', '.join(t.action[:40] for t in fail[:3])}.")
        lines.append(f"• Modified: {', '.join(self.modified_files[:5])}.")
        return "\n".join(lines)


# ── Module-level helpers ──────────────────────────────────────────────────────

def _extract_new_symbols(old_content: str, new_or_replace: str) -> list[str]:
    """
    Find function/class names present in new_or_replace but not in old_content.
    Used for tracking what a patch added.

    Args:
        old_content:    The original file content.
        new_or_replace: The new_string / replace block from the patch.
    """
    def _symbols(code: str) -> set[str]:
        try:
            tree = ast.parse(code)
            return {
                node.name
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            }
        except SyntaxError:
            return set()

    old_syms = _symbols(old_content)
    new_syms = _symbols(new_or_replace)
    return sorted(new_syms - old_syms)
