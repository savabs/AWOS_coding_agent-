"""
post_mortem.py — P8 Reflexion: Learning from Failure.

Implements the full Reflexion loop (NeurIPS 2023, arXiv:2303.11366):
    failure → reflect (verbal critique) → store → recall → inject

Three trigger points in the orchestrator:
    WORKER_FAIL  — worker could not produce a valid patch
    VERIFY_FAIL  — patch produced but verifier rejected it (syntax/apply error)
    TEST_FAIL    — patch applied but tests regressed

Design principles:
    1. Every failure is worth reflecting on — even verifier rejections carry signal.
    2. Recall is semantic (action keywords), not just exact (file_path, error_type).
    3. Deduplication: near-identical critiques for the same file are suppressed.
    4. Graceful degradation: all methods return safely even without an LLM client.
    5. Backwards-compatible: uses existing ErrorPatternStore + SelfCorrectionEngine.

Cross-session memory: critiques persist in .awos/error_patterns.jsonl and are
recalled on every future task touching the same file or action domain.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

_MAX_RECALL = 3        # max past critiques injected per task
_MIN_KEYWORD_LEN = 4   # minimum keyword length for semantic search
_DEDUP_THRESHOLD = 0.8 # suppress new critique if too similar to recent one


# ── Failure taxonomy ──────────────────────────────────────────────────────────

class FailureType(Enum):
    WORKER_FAIL  = "WORKER_FAIL"   # worker parse/apply failure
    VERIFY_FAIL  = "VERIFY_FAIL"   # verifier rejected the patch
    TEST_FAIL    = "TEST_FAIL"     # tests regressed after patch applied


# ── Critique prompt templates per failure type ────────────────────────────────

_PROMPTS: dict[FailureType, str] = {
    FailureType.WORKER_FAIL: """\
An AI coding agent failed to generate a valid patch for the task below.
Write 1-3 sentences explaining (a) WHY this type of failure happens and
(b) one CONCRETE thing the agent should do differently next time.
Do NOT write code.

Task : {action}
File : {file_path}
Error: {error_text}
""",
    FailureType.VERIFY_FAIL: """\
An AI coding agent generated a patch that was rejected by the verifier.
Write 1-3 sentences explaining (a) WHY the patch failed verification and
(b) what the agent must check before producing the old_string / new_string.
Do NOT write code.

Task       : {action}
File       : {file_path}
Verify err : {error_text}
""",
    FailureType.TEST_FAIL: """\
An AI coding agent applied a patch that caused test failures.
Write 1-3 sentences explaining (a) WHAT the patch likely broke and
(b) what the agent should verify about interface contracts before editing.
Do NOT write code.

Task        : {action}
File        : {file_path}
Test output : {error_text}
""",
}


# ── Public API ─────────────────────────────────────────────────────────────────

@dataclass
class PostMortemResult:
    """Result of one reflection pass."""
    critique:     str               # verbal critique (1-3 sentences)
    failure_type: FailureType
    stored:       bool              # True if saved to ErrorPatternStore
    recalled:     list              # past ErrorPatterns injected into prompt


class PostMortemEngine:
    """
    Unified reflect → store → recall lifecycle for all failure types.

    Usage (orchestrator):
        pm = PostMortemEngine(error_store, model_caller=self._cheap_call)

        # on worker/verify/test failure:
        result = pm.reflect(task, error_text, FailureType.TEST_FAIL)

        # before next attempt:
        recalled = pm.recall(task, FailureType.TEST_FAIL)
        retry_task = {**task, "past_critiques": recalled,
                      "error_context": pm.format_for_prompt(recalled) + error_context}
    """

    def __init__(
        self,
        error_store,                        # ErrorPatternStore instance
        model_caller: Optional[Callable[[str], str]] = None,
    ) -> None:
        self._store = error_store
        self._call  = model_caller          # cheap LLM call, None = degrade gracefully

    # ── Reflect ───────────────────────────────────────────────────────────────

    def reflect(
        self,
        task: dict,
        error_text: str,
        failure_type: FailureType,
    ) -> PostMortemResult:
        """
        Generate a verbal critique for a failure and persist it.

        Always returns a PostMortemResult — never raises.
        If LLM call fails, stores an empty critique (still records the error type).
        """
        file_path  = task.get("file", "unknown")
        action     = str(task.get("action", ""))[:200]
        error_text = str(error_text or "")[:600]

        # Skip if we already have a very similar critique for this file
        if self._is_duplicate(file_path, failure_type.value, error_text):
            logger.debug("[post_mortem] skipping duplicate reflect for %s/%s", file_path, failure_type.value)
            recalled = self.recall(task, failure_type)
            return PostMortemResult(
                critique="(duplicate suppressed)",
                failure_type=failure_type,
                stored=False,
                recalled=recalled,
            )

        critique = self._generate_critique(action, file_path, error_text, failure_type)

        stored = False
        if critique:
            try:
                from .error_pattern_store import make_error_pattern
            except ImportError:
                from error_pattern_store import make_error_pattern
            pattern = make_error_pattern(
                task_id    = str(task.get("task_id", "unknown")),
                file_path  = file_path,
                error_type = failure_type.value,
                error_msg  = error_text[:300],
                critique   = critique,
            )
            self._store.save(pattern)
            stored = True
            logger.info(
                "[post_mortem] stored %s critique for %s (%d chars)",
                failure_type.value, file_path, len(critique),
            )

        recalled = self.recall(task, failure_type)
        return PostMortemResult(
            critique=critique,
            failure_type=failure_type,
            stored=stored,
            recalled=recalled,
        )

    # ── Recall ────────────────────────────────────────────────────────────────

    def recall(
        self,
        task: dict,
        failure_type: Optional[FailureType] = None,
        top_n: int = _MAX_RECALL,
    ) -> list:
        """
        Retrieve relevant past critiques for a task.

        Strategy (ordered):
        1. Exact match: (file_path, failure_type.value) → fastest, most specific
        2. File-level match: any error_type for this file
        3. Semantic fallback: action keywords → scan all patterns for keyword hits

        Returns list of ErrorPattern objects (most recent first), capped at top_n.
        """
        file_path = task.get("file", "unknown")

        # 1. Exact match
        if failure_type is not None:
            exact = self._store.retrieve(file_path, failure_type.value, top_n=top_n)
            if exact:
                return exact

        # 2. File-level any-type match
        any_for_file = self._store.retrieve_any(file_path, top_n=top_n)
        if any_for_file:
            return any_for_file

        # 3. Semantic keyword fallback
        return self._semantic_recall(task.get("action", ""), top_n=top_n)

    # ── Format for prompt injection ───────────────────────────────────────────

    @staticmethod
    def format_for_prompt(patterns: list, label: str = "PAST FAILURE CRITIQUES") -> str:
        """
        Format a list of ErrorPattern objects as a prompt block for the generator.

        Returns "" if patterns is empty.
        """
        if not patterns:
            return ""
        lines = [f"[{label} — learn from these mistakes]"]
        for i, p in enumerate(patterns[:_MAX_RECALL], 1):
            ftype = getattr(p, "error_type", "UNKNOWN")
            critique_text = getattr(p, "critique", str(p))[:300]
            lines.append(f"  {i}. [{ftype}] {critique_text}")
        return "\n".join(lines) + "\n"

    # ── Reflexion stats (for LearningInspector) ───────────────────────────────

    def stats(self) -> dict:
        """Return summary counts for the LearningInspector dashboard."""
        summary = self._store.summary()
        total = summary.get("total", 0)
        by_type = summary.get("by_type", {})
        by_file = summary.get("by_file", {})

        most_failing_file = max(by_file, key=by_file.get, default="none") if by_file else "none"
        most_common_type  = max(by_type, key=by_type.get, default="none") if by_type else "none"

        return {
            "total_critiques":    total,
            "by_failure_type":    by_type,
            "files_with_critiques": len(by_file),
            "most_failing_file":  most_failing_file,
            "most_common_type":   most_common_type,
        }

    # ── Private ───────────────────────────────────────────────────────────────

    def _generate_critique(
        self,
        action: str,
        file_path: str,
        error_text: str,
        failure_type: FailureType,
    ) -> str:
        """Call the cheap LLM to generate a verbal critique. Returns "" on any failure."""
        if not self._call:
            return ""
        try:
            template = _PROMPTS[failure_type]
            prompt = template.format(
                action=action,
                file_path=file_path,
                error_text=error_text[:400],
            )
            critique = self._call(prompt)
            return (critique or "").strip()[:400]
        except Exception as exc:
            logger.warning("[post_mortem] generate_critique failed: %s", exc)
            return ""

    def _is_duplicate(
        self,
        file_path: str,
        error_type: str,
        error_text: str,
    ) -> bool:
        """
        Return True if a nearly identical critique was already stored for this
        (file_path, error_type) in the last 5 records.

        Uses simple character overlap — avoids storing redundant critiques when
        the same task retries repeatedly with the same error.
        """
        recent = self._store.retrieve(file_path, error_type, top_n=5)
        if not recent:
            return False
        for p in recent:
            existing = (p.error_msg or "").lower()
            incoming = error_text.lower()
            if not existing or not incoming:
                continue
            # Jaccard similarity on character trigrams
            def trigrams(s: str) -> set:
                return {s[i:i+3] for i in range(len(s)-2)} if len(s) >= 3 else set()
            t1, t2 = trigrams(existing[:200]), trigrams(incoming[:200])
            if t1 and t2:
                sim = len(t1 & t2) / len(t1 | t2)
                if sim >= _DEDUP_THRESHOLD:
                    return True
        return False

    def _semantic_recall(self, action: str, top_n: int = _MAX_RECALL) -> list:
        """
        Scan ErrorPatternStore for patterns whose critique or error_msg contains
        keywords from the action string. Returns top_n by recency.
        """
        keywords = [
            w.lower() for w in re.findall(r"\w+", action)
            if len(w) >= _MIN_KEYWORD_LEN and w.lower() not in
            ("self", "return", "true", "false", "none", "with", "from", "import")
        ]
        if not keywords:
            return []

        # Walk the store and score by keyword overlap
        summary = self._store.summary()
        if summary.get("total", 0) == 0:
            return []

        scored: list[tuple[float, object]] = []
        try:
            # Walk all files in the store
            all_files = list(summary.get("by_file", {}).keys())
            seen_ids: set = set()
            for fp in all_files:
                patterns = self._store.retrieve_any(fp, top_n=20)
                for p in patterns:
                    pid = (getattr(p, "task_id", ""), getattr(p, "timestamp", ""))
                    if pid in seen_ids:
                        continue
                    seen_ids.add(pid)
                    text = (
                        (getattr(p, "critique", "") or "") + " " +
                        (getattr(p, "error_msg", "") or "")
                    ).lower()
                    hits = sum(1 for kw in keywords if kw in text)
                    if hits > 0:
                        scored.append((hits, p))
        except Exception as exc:
            logger.debug("[post_mortem] semantic_recall scan failed: %s", exc)
            return []

        scored.sort(key=lambda x: x[0], reverse=True)
        return [p for _, p in scored[:top_n]]
