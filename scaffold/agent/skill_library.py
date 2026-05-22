"""
SkillLibrary — Auto-generated skill knowledge base from successful task outcomes.

After every verified-successful task, AWOS extracts a high-level pattern:
  - What task type was it?
  - Which model + strategy combination won?
  - What keywords characterised the action?
  - How many attempts were needed?

These patterns are:
  1. Stored in .awos/skills/index.json (persistent, queryable)
  2. Written to .awos/skills/<task_type>.md (human-readable skill file)
  3. Injected into future Worker prompts as a "SKILL CONTEXT" block

This bridges the gap between ExampleStore (SEARCH/REPLACE code pairs) and
the higher-level "what approach works for this kind of task" knowledge.

Usage:
    library = SkillLibrary()

    # After a successful task:
    library.record(task, model_name="DeepSeek V4 Flash",
                   strategy_name="cot", attempts=1)

    # Before a new task:
    context = library.get_skill_context(task)   # inject into prompt
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_SKILLS_DIR = Path(".awos") / "skills"

# ── Task-type signals (mirrors strategy_config, kept local to avoid circular import) ──
_TASK_TYPE_SIGNALS: dict[str, tuple[str, ...]] = {
    "bug_fix":      ("fix", "bug", "error", "crash", "fail", "broken", "wrong", "incorrect"),
    "refactor":     ("refactor", "rename", "move", "extract", "clean", "simplify", "reorganise"),
    "new_feature":  ("add", "create", "implement", "new", "build", "write", "introduce"),
    "architecture": ("architect", "design", "system", "module", "interface", "abstraction", "layer"),
}
TASK_TYPES = list(_TASK_TYPE_SIGNALS.keys()) + ["other"]


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class SkillEntry:
    """One learned pattern from a successful task."""
    task_type:     str
    keywords:      list[str]        # dominant keywords extracted from task action
    model_used:    str
    strategy_used: str
    attempts:      int              # 1 = solved first try
    success_count: int = 1
    total_count:   int = 1
    last_seen:     str = field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d")
    )

    @property
    def win_rate(self) -> float:
        return round(self.success_count / max(self.total_count, 1), 2)

    @property
    def avg_attempts(self) -> float:
        return round(self.attempts / max(self.success_count, 1), 1)


# ── SkillLibrary ──────────────────────────────────────────────────────────────

class SkillLibrary:
    """
    Records successful task patterns and provides skill context for future tasks.

    Storage layout:
        .awos/skills/index.json          — all entries, JSON array
        .awos/skills/bug_fix.md          — human-readable skill file
        .awos/skills/new_feature.md      — etc.
    """

    MAX_ENTRIES = 500
    MAX_CONTEXT_SKILLS = 3   # inject at most 3 skill snippets per prompt

    def __init__(self, skills_dir: Optional[Path] = None) -> None:
        self._dir = skills_dir or _DEFAULT_SKILLS_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._dir / "index.json"
        self._entries: list[SkillEntry] = []
        self._load()

    # ── Public API ────────────────────────────────────────────────────────────

    def record(
        self,
        task: dict,
        model_name: str,
        strategy_name: str,
        attempts: int = 1,
    ) -> None:
        """
        Record a successful task outcome.

        Merges with an existing matching entry (same task_type + top keyword +
        model + strategy) to accumulate statistics, or creates a new entry.
        """
        task_type = _classify(task)
        keywords = _extract_keywords(task.get("action", ""))

        existing = self._find_match(task_type, keywords, model_name, strategy_name)
        if existing is not None:
            existing.success_count += 1
            existing.total_count += 1
            existing.attempts += attempts
            existing.last_seen = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        else:
            entry = SkillEntry(
                task_type=task_type,
                keywords=keywords[:5],
                model_used=model_name,
                strategy_used=strategy_name,
                attempts=attempts,
            )
            self._entries.append(entry)
            if len(self._entries) > self.MAX_ENTRIES:
                self._entries = self._entries[-self.MAX_ENTRIES:]

        self._save()
        self._write_skill_file(task_type)
        logger.debug(
            "[skill_library] recorded task_type=%s model=%s strategy=%s attempts=%d",
            task_type, model_name, strategy_name, attempts,
        )

    def get_skill_context(self, task: dict) -> str:
        """
        Return a short skill context block for injection into the Worker prompt.
        Returns empty string if no relevant skills exist yet.
        """
        task_type = _classify(task)
        keywords = set(_extract_keywords(task.get("action", "")))

        # Score entries: same task_type + keyword overlap
        scored: list[tuple[float, SkillEntry]] = []
        for e in self._entries:
            if e.task_type != task_type:
                continue
            overlap = len(keywords & set(e.keywords))
            score = overlap + e.win_rate * 0.5
            if score > 0:
                scored.append((score, e))

        if not scored:
            return ""

        scored.sort(key=lambda x: x[0], reverse=True)
        top = [e for _, e in scored[: self.MAX_CONTEXT_SKILLS]]

        lines = [f"SKILL CONTEXT for {task_type} tasks (from past successes):"]
        for e in top:
            kw_str = ", ".join(e.keywords[:3]) if e.keywords else "general"
            lines.append(
                f"  • [{e.model_used} + {e.strategy_used}] "
                f"won {e.success_count}× on tasks like '{kw_str}' "
                f"(avg {e.avg_attempts} attempt(s))"
            )

        return "\n".join(lines)

    def summary(self) -> dict:
        """Return a count-grouped summary for display/tests."""
        by_type: dict[str, list[dict]] = {}
        for e in self._entries:
            by_type.setdefault(e.task_type, []).append({
                "keywords": e.keywords,
                "model": e.model_used,
                "strategy": e.strategy_used,
                "win_rate": e.win_rate,
                "success_count": e.success_count,
            })
        return by_type

    def total_entries(self) -> int:
        return len(self._entries)

    # ── Skill file writer ─────────────────────────────────────────────────────

    def _write_skill_file(self, task_type: str) -> None:
        """Write/overwrite .awos/skills/<task_type>.md."""
        entries = [e for e in self._entries if e.task_type == task_type]
        if not entries:
            return

        # Sort by win_rate descending
        entries.sort(key=lambda e: (e.win_rate, e.success_count), reverse=True)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        lines = [
            f"# AWOS Skills: {task_type}",
            f"",
            f"Auto-generated from successful task outcomes. Last updated: {today}.",
            f"Do not edit manually — this file is overwritten on each new success.",
            f"",
            f"## Summary ({len(entries)} pattern(s))",
            f"",
        ]

        for e in entries[:20]:  # cap at 20 patterns per file
            kw_str = ", ".join(e.keywords[:4]) if e.keywords else "(general)"
            lines += [
                f"### Pattern: {kw_str}",
                f"- **Model**: {e.model_used}",
                f"- **Strategy**: {e.strategy_used}",
                f"- **Win rate**: {e.win_rate * 100:.0f}% ({e.success_count}/{e.total_count})",
                f"- **Avg attempts**: {e.avg_attempts}",
                f"- **Last seen**: {e.last_seen}",
                f"",
            ]

        skill_path = self._dir / f"{task_type}.md"
        try:
            skill_path.write_text("\n".join(lines), encoding="utf-8")
            logger.debug("[skill_library] wrote %s", skill_path)
        except OSError as exc:
            logger.warning("[skill_library] could not write skill file: %s", exc)

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save(self) -> None:
        try:
            self._index_path.write_text(
                json.dumps([asdict(e) for e in self._entries], indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("[skill_library] save failed: %s", exc)

    def _load(self) -> None:
        if not self._index_path.exists():
            return
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
            self._entries = [SkillEntry(**d) for d in data]
        except Exception as exc:
            logger.warning("[skill_library] load failed: %s", exc)
            self._entries = []

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _find_match(
        self,
        task_type: str,
        keywords: list[str],
        model_name: str,
        strategy_name: str,
    ) -> Optional[SkillEntry]:
        """Find an existing entry with same task_type, top keyword, model, strategy."""
        top_kw = keywords[0] if keywords else ""
        for e in self._entries:
            if (
                e.task_type == task_type
                and e.model_used == model_name
                and e.strategy_used == strategy_name
                and (top_kw in e.keywords or not top_kw)
            ):
                return e
        return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _classify(task: dict) -> str:
    """Map task dict to a TASK_TYPE string."""
    action = task.get("action", "").lower()
    for task_type, signals in _TASK_TYPE_SIGNALS.items():
        if any(sig in action for sig in signals):
            return task_type
    return "other"


_STOPWORDS = frozenset({
    "a", "an", "the", "to", "in", "for", "of", "and", "or",
    "is", "it", "this", "that", "with", "add", "update", "fix",
    "make", "into", "from", "using", "when", "should", "will",
})


def _extract_keywords(text: str) -> list[str]:
    """Extract meaningful keywords, ordered by length (more specific first)."""
    words = re.findall(r"[a-z_][a-z0-9_]*", text.lower())
    filtered = [w for w in words if len(w) > 3 and w not in _STOPWORDS]
    seen: dict[str, None] = {}
    for w in filtered:
        seen[w] = None
    return sorted(seen.keys(), key=len, reverse=True)[:8]
