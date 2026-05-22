"""
ExampleStore: Few-shot example injection for intelligence amplification.

Core idea:
  A DeepSeek prompt with 2 real examples from this codebase outperforms
  a Claude Opus prompt with no examples. Examples are free context — they
  teach the model the exact format and patterns used in THIS project.

What it stores:
  - Successful SEARCH/REPLACE pairs (task → search → replace)
  - Indexed by task keyword similarity
  - Persisted to disk as JSON (grows over time)

What it gives back:
  - 2 most relevant examples injected into the worker prompt
  - Teaches format compliance (prevents regex failures)
  - Teaches project conventions (naming, style, patterns)
"""

import json
import os
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


@dataclass
class StoredExample:
    task_action:  str   # Original task description
    file_ext:     str   # .py, .js, etc.
    search:       str   # Exact SEARCH string used
    replace:      str   # Exact REPLACE string
    keywords:     list  # Extracted keywords for retrieval


class ExampleStore:
    """Stores and retrieves successful SEARCH/REPLACE examples."""

    MAX_EXAMPLES = 200      # Cap to avoid huge files
    MAX_PER_PROMPT = 2      # Inject at most 2 per worker call

    def __init__(self, store_path: Optional[str] = None):
        if store_path is None:
            store_path = str(Path.home() / ".awos" / "examples.json")
        self.store_path = Path(store_path)
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self._examples: list[StoredExample] = []
        self._load()

    # ── Public API ────────────────────────────────────────────────────────────

    def record_success(self, task: dict, search: str, replace: str):
        """Save a successful SEARCH/REPLACE for future use."""
        action = task.get("action", "")
        file_path = task.get("file", "")
        ext = Path(file_path).suffix or ".py"
        keywords = _extract_keywords(action)

        example = StoredExample(
            task_action=action[:200],
            file_ext=ext,
            search=search[:500],    # Truncate long examples
            replace=replace[:500],
            keywords=keywords,
        )
        self._examples.append(example)

        # Keep under cap (evict oldest)
        if len(self._examples) > self.MAX_EXAMPLES:
            self._examples = self._examples[-self.MAX_EXAMPLES:]

        self._save()

    def get_examples_for_prompt(self, task: dict) -> str:
        """
        Return a formatted few-shot block for injection into the worker prompt.
        Returns empty string if no relevant examples exist.
        """
        relevant = self._retrieve(task, top_k=self.MAX_PER_PROMPT)
        if not relevant:
            return ""

        lines = ["EXAMPLES FROM THIS CODEBASE (use as format reference):"]
        for i, ex in enumerate(relevant, 1):
            lines.append(f"\nExample {i} — task: {ex.task_action[:80]}")
            lines.append(f"SEARCH:\n```\n{ex.search}\n```")
            lines.append(f"REPLACE:\n```\n{ex.replace}\n```")

        return "\n".join(lines)

    def size(self) -> int:
        return len(self._examples)

    def recent(self, n: int = 5) -> list:
        """Return the last n examples as plain dicts."""
        return [asdict(e) for e in self._examples[-n:]]

    def clear(self):
        """Reset _examples to an empty list and delete the store file if it exists."""
        self._examples = []
        if self.store_path.exists():
            self.store_path.unlink()

    # ── Retrieval ─────────────────────────────────────────────────────────────

    def _retrieve(self, task: dict, top_k: int = 2) -> list[StoredExample]:
        """Return top_k most similar examples by keyword overlap."""
        if not self._examples:
            return []

        action = task.get("action", "")
        file_path = task.get("file", "")
        ext = Path(file_path).suffix or ".py"
        query_kws = set(_extract_keywords(action))

        scored = []
        for ex in self._examples:
            if ex.file_ext != ext:
                continue
            overlap = len(query_kws & set(ex.keywords))
            if overlap > 0:
                scored.append((overlap, ex))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [ex for _, ex in scored[:top_k]]

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save(self):
        try:
            data = [asdict(e) for e in self._examples]
            self.store_path.write_text(json.dumps(data, indent=2))
        except Exception:
            pass

    def _load(self):
        try:
            if self.store_path.exists():
                data = json.loads(self.store_path.read_text())
                self._examples = [StoredExample(**d) for d in data]
        except Exception:
            self._examples = []


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_keywords(text: str) -> list:
    """Extract meaningful keywords for similarity matching."""
    stopwords = {"a", "an", "the", "to", "in", "for", "of", "and", "or",
                 "is", "it", "this", "that", "with", "add", "update", "fix"}
    words = re.findall(r"[a-z]+", text.lower())
    return [w for w in words if len(w) > 3 and w not in stopwords]
