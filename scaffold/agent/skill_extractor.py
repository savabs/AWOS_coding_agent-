"""
skill_extractor.py — Auto skill extraction from successful task executions (P3.2).

Extracts reusable patterns from high-confidence completed tasks, saves them as
Markdown+YAML skill files in .awos/skills/. Matches Claude Code / Hermes pattern.

Usage:
    extractor = SkillExtractor()
    skill_path = extractor.maybe_extract_skill(
        task={"action": "...", "file": "...", "complexity": "medium"},
        result={"score": 0.95, "key_change": "added login method"},
        cheap_llm_caller=lambda prompt, max_tokens: llm(prompt),
    )
    # Returns skill file path if created/updated, None otherwise

    library = SkillLibrary()
    skills = library.get_relevant_skills(task, top_k=2)
    # Returns list of skill content strings to inject into worker prompt
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)


# ── Skill Extractor ───────────────────────────────────────────────────────────

class SkillExtractor:
    """
    Extract reusable patterns from successful task executions.
    Saves YAML+Markdown skill files to .awos/skills/.
    """

    SKILLS_DIR = ".awos/skills"
    MIN_CONFIDENCE = 0.8

    def maybe_extract_skill(
        self,
        task: dict,
        result: dict,
        cheap_llm_caller: Optional[Callable[[str, int], str]] = None,
    ) -> Optional[str]:
        """
        Extract a skill from a successful task if score >= MIN_CONFIDENCE.
        Returns the skill file path if created/updated, None otherwise.
        """
        score = result.get("score", 0)
        if score < self.MIN_CONFIDENCE:
            return None

        skill_key = self._compute_skill_key(task)
        existing = self._find_similar_skill(skill_key)
        if existing:
            self._update_skill_stats(existing, success=True)
            logger.debug("[SkillExtractor] updated existing skill: %s", existing)
            return existing

        if cheap_llm_caller is None:
            skill_content = self._fallback_skill(task, result)
        else:
            skill_content = self._generate_skill(task, result, cheap_llm_caller)

        if not skill_content:
            return None

        path = self._save_skill(skill_key, skill_content)
        logger.info("[SkillExtractor] saved new skill: %s", path)
        return path

    # ── Private helpers ───────────────────────────────────────────────────────

    def _generate_skill(
        self,
        task: dict,
        result: dict,
        cheap_llm_caller: Callable[[str, int], str],
    ) -> Optional[str]:
        """Call cheap LLM to generate a skill description."""
        prompt = f"""Extract a reusable skill pattern from this successful coding task.

Task: {task.get('action', '')}
File: {task.get('file', '')}
Complexity: {task.get('complexity', 'medium')}
What changed: {result.get('key_change', result.get('reasoning', ''))}
New symbols: {result.get('new_symbols', [])}

Write a concise skill in YAML+Markdown format:
---
title: "[skill title]"
tags: [tag1, tag2, tag3]
complexity: {task.get('complexity', 'medium')}
---

## When to use
[1-2 sentences describing when this skill applies]

## Pattern
[3-5 numbered steps]

## Common pitfalls
[1-3 mistakes to avoid]

Maximum 200 words. Focus on the PATTERN, not this specific task."""
        try:
            return cheap_llm_caller(prompt, 400)
        except Exception:
            return None

    def _fallback_skill(self, task: dict, result: dict) -> str:
        """Generate a minimal skill file without LLM (deterministic)."""
        action = task.get("action", "task")
        file = task.get("file", "unknown")
        complexity = task.get("complexity", "medium")
        return (
            f"---\ntitle: \"{action[:60]}\"\n"
            f"tags: [auto-extracted]\ncomplexity: {complexity}\n---\n\n"
            f"## When to use\nUse when doing: {action}\n\n"
            f"## Pattern\n1. Edit {file}\n2. Apply the change\n\n"
            f"## Common pitfalls\n- Verify file path exists before editing\n"
        )

    def _compute_skill_key(self, task: dict) -> str:
        """Normalized key for deduplication (strips specific names)."""
        action = task.get("action", "").lower()
        normalized = re.sub(r'`[^`]+`', '<symbol>', action)
        normalized = re.sub(r'"[^"]+"', '<name>', normalized)
        return hashlib.md5(normalized.encode()).hexdigest()[:12]

    def _find_similar_skill(self, key: str) -> Optional[str]:
        """Return path to existing skill file with matching key suffix, or None."""
        skills_dir = Path(self.SKILLS_DIR)
        if not skills_dir.exists():
            return None
        for sf in skills_dir.glob(f"*_{key[:6]}.md"):
            return str(sf)
        return None

    def _update_skill_stats(self, skill_path: str, success: bool) -> None:
        """Increment usage_count in skill frontmatter."""
        try:
            p = Path(skill_path)
            content = p.read_text(encoding="utf-8")
            count_match = re.search(r'usage_count:\s*(\d+)', content)
            if count_match:
                old_count = int(count_match.group(1))
                content = content.replace(
                    count_match.group(0), f"usage_count: {old_count + 1}"
                )
                p.write_text(content, encoding="utf-8")
        except Exception:
            pass

    def _save_skill(self, key: str, content: str) -> str:
        """Save skill content to .awos/skills/{title}_{key}.md"""
        skills_dir = Path(self.SKILLS_DIR)
        skills_dir.mkdir(parents=True, exist_ok=True)

        title_match = re.search(r'title:\s*"([^"]+)"', content)
        title = title_match.group(1) if title_match else key
        filename = re.sub(r'[^a-z0-9_-]', '_', title.lower())[:40]
        skill_path = skills_dir / f"{filename}_{key[:6]}.md"
        skill_path.write_text(content, encoding="utf-8")
        return str(skill_path)


# ── Skill Library ─────────────────────────────────────────────────────────────

class SkillLibrary:
    """
    Load and match skills for injection into worker prompts.
    Keyword-overlap scoring — no LLM needed.
    """

    SKILLS_DIR = ".awos/skills"

    def get_relevant_skills(self, task: dict, top_k: int = 2) -> List[str]:
        """
        Return top-k most relevant skill contents for a task action.
        Returns empty list if .awos/skills/ doesn't exist or is empty.
        """
        skills_dir = Path(self.SKILLS_DIR)
        if not skills_dir.exists():
            return []
        skill_files = list(skills_dir.glob("*.md"))
        if not skill_files:
            return []

        task_words = set(task.get("action", "").lower().split())
        scored: list[tuple[float, str]] = []
        for sf in skill_files:
            try:
                content = sf.read_text(encoding="utf-8", errors="ignore")
                skill_words = set(content.lower().split())
                overlap = len(task_words & skill_words) / max(len(task_words), 1)
                scored.append((overlap, content))
            except Exception:
                continue

        scored.sort(reverse=True)
        return [content for _, content in scored[:top_k] if _ > 0]

    def format_for_prompt(self, skills: List[str]) -> str:
        """Format skill snippets for injection into worker Block A."""
        if not skills:
            return ""
        lines = ["## Relevant Skills from Past Executions:"]
        for i, skill in enumerate(skills, 1):
            lines.append(f"\n### Skill {i}:\n{skill[:600]}")
        return "\n".join(lines)
