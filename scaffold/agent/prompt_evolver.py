"""
PromptEvolver — OPRO-inspired self-modifying worker guidelines.

After every AWOS_PROMPT_EVOLVE_EVERY sessions (default 10), reads
ErrorPatternStore and SkillLibrary, asks a cheap LLM to propose targeted
additional guidelines, and persists them to .awos/evolved_prompt.json.

Worker.execute_task hot-loads and injects these guidelines before each LLM
call — no process restart required.

Feature 1A from tasks/active/true_self_learning_task.md.
Reference: OPRO (arXiv:2309.03409), PromptBreeder (arXiv:2309.16797)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

_EVOLVED_PROMPT_FILE = "evolved_prompt.json"
_DEFAULT_EVOLVE_EVERY = 10
_MAX_PATTERNS = 50
_MIN_CONFIDENCE = 0.7
_MAX_SKILLS = 5
_MAX_GUIDELINES_CHARS = 600


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class _Mutation:
    """One proposed guideline change."""
    section: str
    guideline: str
    reason: str
    confidence: float


class PromptEvolver:
    """
    Evolves the worker's runtime guidelines from empirical session outcomes.

    Data sources:
        .awos/error_patterns.jsonl  — failure critiques (ErrorPatternStore)
        .awos/skills/index.json     — success patterns  (SkillLibrary)

    Output:
        .awos/evolved_prompt.json   — hot-loaded by Worker.execute_task
    """

    def __init__(
        self,
        store_path: str = ".awos",
        cheap_call: Optional[Callable[[str], str]] = None,
    ) -> None:
        self._store = Path(store_path)
        self._evolved_path = self._store / _EVOLVED_PROMPT_FILE
        self._patterns_path = self._store / "error_patterns.jsonl"
        self._skills_path = self._store / "skills" / "index.json"
        self._cheap_call = cheap_call
        self._evolve_every = int(
            os.getenv("AWOS_PROMPT_EVOLVE_EVERY", str(_DEFAULT_EVOLVE_EVERY))
        )

    # ── Public API ──────────────────────────────────────────────────────────

    def should_evolve(self, session_count: int) -> bool:
        """True every AWOS_PROMPT_EVOLVE_EVERY sessions when feature is enabled."""
        if os.getenv("AWOS_PROMPT_EVOLUTION", "").lower() != "true":
            return False
        if self._cheap_call is None:
            return False
        return session_count > 0 and (session_count % self._evolve_every == 0)

    def evolve(
        self,
        error_patterns_raw: Optional[List[dict]] = None,
        skill_entries_raw: Optional[List[dict]] = None,
    ) -> str:
        """
        Generate evolved guidelines from failure/success evidence.

        Returns a non-empty guidelines string, or '' if no confident
        improvements were found or any step fails.
        """
        if self._cheap_call is None:
            return ""

        patterns = error_patterns_raw if error_patterns_raw is not None else self._read_error_patterns()
        skills   = skill_entries_raw   if skill_entries_raw   is not None else self._read_skill_entries()

        if not patterns and not skills:
            logger.debug("[PromptEvolver] no evidence — skipping")
            return ""

        failure_summary = self._summarise_failures(patterns)
        success_summary = self._summarise_successes(skills)
        meta_prompt     = self._build_meta_prompt(failure_summary, success_summary, len(patterns))

        try:
            raw = self._cheap_call(meta_prompt)
            mutations = self._parse_mutations(raw)
        except Exception as exc:
            logger.warning("[PromptEvolver] LLM/parse failed: %s", exc)
            return ""

        if not mutations:
            logger.info("[PromptEvolver] no confident mutations")
            return ""

        guidelines = "\n".join(
            f"- {m.guideline}" for m in mutations
        )
        return guidelines[:_MAX_GUIDELINES_CHARS]

    def persist(self, guidelines: str, session_count: int) -> None:
        """Save evolved guidelines to .awos/evolved_prompt.json."""
        self._store.mkdir(parents=True, exist_ok=True)
        payload = {
            "evolved_guidelines": guidelines,
            "evolved_at": _now_iso(),
            "session_count": session_count,
            "changes_applied": len([l for l in guidelines.splitlines() if l.strip()]),
        }
        self._evolved_path.write_text(json.dumps(payload, indent=2))
        logger.info("[PromptEvolver] persisted %d guideline lines", payload["changes_applied"])

    def load_evolved_guidelines(self) -> str:
        """
        Hot-load evolved guidelines from .awos/evolved_prompt.json.
        Returns '' if file missing, corrupt, or empty.
        """
        if not self._evolved_path.exists():
            return ""
        try:
            data = json.loads(self._evolved_path.read_text())
            return data.get("evolved_guidelines", "").strip()
        except Exception as exc:
            logger.debug("[PromptEvolver] load failed: %s", exc)
            return ""

    # ── Internal helpers ────────────────────────────────────────────────────

    def _read_error_patterns(self) -> List[dict]:
        if not self._patterns_path.exists():
            return []
        records: List[dict] = []
        try:
            for line in self._patterns_path.read_text().splitlines()[-_MAX_PATTERNS:]:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        except Exception as exc:
            logger.debug("[PromptEvolver] patterns read error: %s", exc)
        return records

    def _read_skill_entries(self) -> List[dict]:
        if not self._skills_path.exists():
            return []
        try:
            data = json.loads(self._skills_path.read_text())
            return data if isinstance(data, list) else list(data.values())
        except Exception as exc:
            logger.debug("[PromptEvolver] skills read error: %s", exc)
        return []

    def _summarise_failures(self, patterns: List[dict]) -> str:
        counts: dict[str, int] = {}
        samples: dict[str, list] = {}
        for p in patterns:
            et = p.get("error_type", "UNKNOWN")
            counts[et] = counts.get(et, 0) + 1
            if et not in samples:
                samples[et] = []
            if len(samples[et]) < 2:
                c = p.get("critique", p.get("error_msg", ""))[:150]
                if c:
                    samples[et].append(c)
        lines = []
        for et, cnt in sorted(counts.items(), key=lambda x: -x[1])[:5]:
            ex = "; ".join(samples.get(et, []))[:200]
            lines.append(f"  {et}: {cnt} times. Example: {ex}")
        return "\n".join(lines) or "  (none)"

    def _summarise_successes(self, skills: List[dict]) -> str:
        lines = []
        for sk in sorted(skills, key=lambda x: x.get("win_rate", 0), reverse=True)[:_MAX_SKILLS]:
            tt    = sk.get("task_type", "?")
            kw    = ", ".join(sk.get("keywords", [])[:4])
            model = sk.get("model_used", "?")
            wr    = sk.get("win_rate", 0)
            lines.append(f"  {tt} ({kw}) via {model}: {wr:.0%} win rate")
        return "\n".join(lines) or "  (none)"

    def _build_meta_prompt(
        self, failure_summary: str, success_summary: str, n_records: int
    ) -> str:
        return f"""You are improving a code-editing agent's runtime guidelines.

The agent writes SEARCH/REPLACE code patches. It executes code-editing tasks
and sometimes fails. You must suggest concise, actionable guideline improvements
based on the evidence below.

EVIDENCE FROM {n_records} TASK OUTCOMES:
Top failure patterns:
{failure_summary}

Top success patterns:
{success_summary}

Propose up to 3 concise, targeted guidelines (1 sentence each) that address
the most frequent failures. Only include guidelines with confidence >= {_MIN_CONFIDENCE}.
Tie every guideline directly to the evidence.

Output ONLY valid JSON (no markdown, no explanation outside JSON):
{{
  "guidelines": [
    {{
      "section": "<brief label>",
      "guideline": "<one actionable sentence for the agent>",
      "reason": "<evidence reference>",
      "confidence": 0.0
    }}
  ]
}}

If no changes are confident enough, output: {{}}"""

    def _parse_mutations(self, raw: str) -> List[_Mutation]:
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:])
        if raw.endswith("```"):
            raw = "\n".join(raw.split("\n")[:-1])
        raw = raw.strip()

        if not raw or raw == "{}":
            return []

        data = json.loads(raw)
        items = data.get("guidelines", [])
        if not isinstance(items, list):
            return []

        mutations = []
        for item in items:
            conf = float(item.get("confidence", 0.0))
            gl   = item.get("guideline", "").strip()
            if conf >= _MIN_CONFIDENCE and gl:
                mutations.append(_Mutation(
                    section=item.get("section", ""),
                    guideline=gl,
                    reason=item.get("reason", ""),
                    confidence=conf,
                ))
        return mutations
