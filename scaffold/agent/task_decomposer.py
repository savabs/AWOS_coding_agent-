"""
task_decomposer.py — Retry with Simplification for the Orchestrator.

When a task fails at max escalation, break it into smaller sub-tasks
with reduced complexity scores. Each sub-task gets a fresh attempt.

Max 2 decomposition cycles to prevent infinite loops.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


class TaskDecomposer:
    """
    Break a complex failing task into simpler sub-tasks.

    Two strategies (tried in order):
      1. LLM-based: ask a cheap model to split the task
      2. Heuristic: split by action verbs, files, and conjunctions
    """

    def __init__(self, llm_caller=None) -> None:
        """
        Args:
            llm_caller: Callable(prompt: str) -> str for cheap decomposition.
                        If None, heuristic fallback is used exclusively.
        """
        self._llm = llm_caller

    # ── Public API ─────────────────────────────────────────────────────────

    def decompose(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        """
        Break *task* into 2-4 simpler sub-tasks.

        Each sub-task inherits the original file unless the heuristic
        discovers a new file target. Complexity is lowered by 2-3 points.
        """
        # Try LLM first
        if self._llm is not None:
            try:
                sub_tasks = self._decompose_llm(task)
                if sub_tasks:
                    return sub_tasks
            except Exception as e:
                logger.warning("LLM decomposition failed: %s", e)

        # Fallback to heuristic
        return self._decompose_heuristic(task)

    # ── LLM Strategy ─────────────────────────────────────────────────────────

    def _decompose_llm(self, task: dict[str, Any]) -> list[dict[str, Any]] | None:
        prompt = f"""Break this coding task into 2-4 smaller, simpler sub-tasks.
Each sub-task should be independent and easier than the original.

Original task: {task['action']}
File: {task.get('file', 'unknown')}
Complexity: {task.get('complexity', 'medium')}

Return ONLY a JSON array of objects, each with:
  "action": (string) what to do
  "file": (string) target file
  "complexity": "low" or "medium" (never "high")

Example:
[
  {{"action": "Create auth module with basic structure", "file": "auth.py", "complexity": "low"}},
  {{"action": "Add login function to auth module", "file": "auth.py", "complexity": "low"}},
  {{"action": "Wire auth into main app", "file": "app.py", "complexity": "medium"}}
]"""

        raw = self._llm(prompt)
        # Extract JSON from possible markdown fences
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            return None
        data = json.loads(match.group())
        if not isinstance(data, list) or len(data) < 2:
            return None

        sub_tasks = []
        for i, sub in enumerate(data, start=1):
            sub_tasks.append({
                "task_id": f"{task.get('task_id', 'sub')}_{i}",
                "action": sub.get("action", "sub-task"),
                "file": sub.get("file", task.get("file", "unknown")),
                "complexity": sub.get("complexity", "low"),
                "parent_task": task.get("task_id", "unknown"),
                "decomposition_depth": task.get("decomposition_depth", 0) + 1,
            })
        return sub_tasks

    # ── Heuristic Strategy ───────────────────────────────────────────────────

    def _decompose_heuristic(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        action = task.get("action", "")
        file_path = task.get("file", "unknown")
        task_id = task.get("task_id", "sub")
        depth = task.get("decomposition_depth", 0)

        # Split by conjunctions that indicate sequential steps
        delimiters = r"\b(and then|then|and|followed by|after that)\b"
        parts = [p.strip() for p in re.split(delimiters, action, flags=re.IGNORECASE) if p.strip()]

        # If no clear split, split by file mentions or action verbs
        if len(parts) < 2:
            parts = self._split_by_verbs(action)

        # Ensure at least 2 sub-tasks, cap at 4
        if len(parts) < 2:
            parts = [f"Prepare {action}", f"Complete {action}"]

        parts = parts[:4]

        sub_tasks = []
        for i, part in enumerate(parts, start=1):
            sub_tasks.append({
                "task_id": f"{task_id}_{i}",
                "action": part,
                "file": file_path,
                "complexity": "low",  # always lower
                "parent_task": task_id,
                "decomposition_depth": depth + 1,
            })

        return sub_tasks

    @staticmethod
    def _split_by_verbs(text: str) -> list[str]:
        """Split action text by strong verb boundaries."""
        # Common coding action verbs that often indicate separate steps
        split_verbs = [
            "implement", "create", "add", "build", "write",
            "refactor", "extract", "move", "rename", "update",
            "fix", "optimize", "integrate", "wire", "connect",
        ]

        # Find verb positions (case-insensitive)
        positions = []
        for verb in split_verbs:
            for match in re.finditer(rf"\b{verb}\b", text, re.IGNORECASE):
                positions.append((match.start(), verb, match.group()))

        positions.sort()

        if len(positions) < 2:
            return [text]

        chunks = []
        for i, (pos, _verb, word) in enumerate(positions):
            if i == len(positions) - 1:
                # Last verb: take to end
                chunk = text[pos:].strip()
            else:
                next_pos = positions[i + 1][0]
                chunk = text[pos:next_pos].strip()
            if chunk:
                chunks.append(chunk)

        return chunks
