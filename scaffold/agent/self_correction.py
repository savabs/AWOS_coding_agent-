"""
SelfCorrectionEngine — rule-based failure analysis and retry guidance.

When the Worker fails, instead of blindly retrying with a bigger model,
AWOS now classifies *why* it failed and injects a targeted correction
hint into the next attempt's prompt.

No LLM call required — all corrections are pattern-matched and deterministic.
This keeps the correction loop fast and free.

Error classes:
  SEARCH_NOT_FOUND  — exact SEARCH text not present in file
  SYNTAX_ERROR      — introduced SyntaxError / IndentationError
  FORMAT_MALFORMED  — output missing SEARCH: / REPLACE: blocks
  EMPTY_OUTPUT      — worker returned nothing useful
  UNKNOWN           — catch-all: suggest a fresh minimal approach

Usage:
    engine = SelfCorrectionEngine()
    hint = engine.suggest(task, error_text, file_content, attempt=2)
    next_task = {**task, "error_context": hint.format_for_prompt()}
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# ── Error taxonomy ────────────────────────────────────────────────────────────

class ErrorClass(Enum):
    SEARCH_NOT_FOUND = auto()
    SYNTAX_ERROR     = auto()
    FORMAT_MALFORMED = auto()
    EMPTY_OUTPUT     = auto()
    FILE_NOT_FOUND   = auto()
    VERIFY_FAIL      = auto()
    WORKER_FAIL      = auto()
    UNKNOWN          = auto()


# Ordered list of (trigger_signals, ErrorClass)
_CLASSIFIERS: list[tuple[list[str], ErrorClass]] = [
    (
        ["file not found", "no such file", "does not exist", "cannot find file"],
        ErrorClass.FILE_NOT_FOUND,
    ),
    (
        ["verification failed", "verify failed", "verifier rejected", "apply failed",
         "verification error", "failed verification"],
        ErrorClass.VERIFY_FAIL,
    ),
    (
        ["worker failed", "worker error", "forced worker", "gauntlet g3"],
        ErrorClass.WORKER_FAIL,
    ),
    (
        ["not found", "no match", "search text not", "couldn't find",
         "could not find", "exact text", "search string"],
        ErrorClass.SEARCH_NOT_FOUND,
    ),
    (
        ["syntaxerror", "indentationerror", "invalid syntax",
         "unexpected indent", "syntax error", "unexpected eof"],
        ErrorClass.SYNTAX_ERROR,
    ),
    (
        ["missing search", "missing replace", "no search block",
         "no replace block", "output format", "parse error", "malformed",
         "could not parse"],
        ErrorClass.FORMAT_MALFORMED,
    ),
    (
        ["empty", "no output", "no change", "no code", "nothing returned",
         "blank response"],
        ErrorClass.EMPTY_OUTPUT,
    ),
]


# ── Correction recipes ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Recipe:
    approach_name: str
    hint: str
    focus_hint: str


_RECIPES: dict[ErrorClass, _Recipe] = {
    ErrorClass.SEARCH_NOT_FOUND: _Recipe(
        approach_name="targeted_search",
        hint=(
            "The SEARCH text was not found verbatim in the file. "
            "Choose a shorter, more unique 2-3 line snippet that appears exactly once. "
            "Avoid lines with variable indentation or trailing whitespace. "
            "Copy the text character-for-character from the file context shown above."
        ),
        focus_hint="Use the function/method signature line as an anchor — it is always unique.",
    ),
    ErrorClass.SYNTAX_ERROR: _Recipe(
        approach_name="syntax_repair",
        hint=(
            "A syntax error was introduced. Produce a minimal SEARCH/REPLACE that "
            "corrects only the broken line(s). Do not change any other logic. "
            "Check: indentation (4 spaces), missing colons, unmatched brackets/parentheses."
        ),
        focus_hint="Fix only the syntax error; leave all surrounding code unchanged.",
    ),
    ErrorClass.FORMAT_MALFORMED: _Recipe(
        approach_name="format_strict",
        hint=(
            "The output format was incorrect. Produce ONLY the SEARCH/REPLACE block. "
            "No introduction, no explanation before SEARCH:. "
            "Start your response directly with 'SEARCH:' on its own line."
        ),
        focus_hint="Output structure: SEARCH: ```...``` REPLACE: ```...``` REASONING: one sentence.",
    ),
    ErrorClass.EMPTY_OUTPUT: _Recipe(
        approach_name="minimal_change",
        hint=(
            "The previous output contained no usable code change. "
            "Make the smallest possible single-line modification that fulfils the task. "
            "If unsure where to change, search for the relevant function name first."
        ),
        focus_hint="Target a single function body or class method — do not change the entire file.",
    ),
    ErrorClass.FILE_NOT_FOUND: _Recipe(
        approach_name="path_check",
        hint=(
            "The target file path does not exist. Confirm the file path from the task "
            "and project layout before editing; create the file only if the task requires it."
        ),
        focus_hint="Use an existing file path from the codebase context.",
    ),
    ErrorClass.VERIFY_FAIL: _Recipe(
        approach_name="verify_repair",
        hint=(
            "The patch failed verification. Ensure SEARCH text matches the file exactly "
            "and the REPLACE block is valid Python with correct indentation."
        ),
        focus_hint="Re-read the file snippet and produce a minimal fix that applies cleanly.",
    ),
    ErrorClass.WORKER_FAIL: _Recipe(
        approach_name="worker_retry",
        hint=(
            "The worker could not produce a valid patch. Try a smaller SEARCH block "
            "anchored on a unique function or class definition."
        ),
        focus_hint="Target one function body; avoid editing unrelated parts of the file.",
    ),
    ErrorClass.UNKNOWN: _Recipe(
        approach_name="fresh_approach",
        hint=(
            "The previous approach failed entirely. Step back: identify the single most "
            "critical line or block that must change. Try a different location — "
            "the previous SEARCH may have targeted the wrong part of the file."
        ),
        focus_hint="Ask yourself: what is the one line that, if changed, would satisfy the task?",
    ),
}


# ── Public API ────────────────────────────────────────────────────────────────

@dataclass
class CorrectionHint:
    """Result of a self-correction analysis."""
    error_class:   ErrorClass
    approach_name: str
    hint:          str
    focus_hint:    str

    def format_for_prompt(self) -> str:
        """Format as a block injected into the Worker's retry prompt."""
        return (
            f"[SELF-CORRECTION — approach: {self.approach_name}]\n"
            f"{self.hint}\n"
            f"Focus: {self.focus_hint}"
        )


_CRITIQUE_PROMPT_TEMPLATE = """\
You are a senior code-review assistant helping an AI coding agent learn from mistakes.

The agent failed to edit the file below. Analyse the failure and write 1-3 concise
sentences explaining (a) WHY it failed and (b) what the agent should do DIFFERENTLY
on the next attempt. Be specific — mention file path, error type, and the concrete
corrective action. Do not write code.

Task action : {action}
Target file  : {file_path}
Error class  : {error_class}
Error message: {error_msg}
"""


class SelfCorrectionEngine:
    """
    Classifies Worker failures and generates targeted correction hints.

    Rule-based (no LLM call). Hints are injected into `task['error_context']`
    for the next retry attempt so the Worker understands *what went wrong*
    and *what to try differently*.

    Phase 5B addition: generate_critique() calls a cheap LLM to produce a
    verbal critique that is stored in ErrorPatternStore for cross-session recall.
    """

    def classify(self, error: str) -> ErrorClass:
        """Map an error string to an ErrorClass via keyword matching."""
        lower = error.lower()
        for signals, cls in _CLASSIFIERS:
            if any(sig in lower for sig in signals):
                return cls
        return ErrorClass.UNKNOWN

    def suggest(
        self,
        task: dict,
        error: str,
        file_content: str,
        attempt: int,
    ) -> CorrectionHint:
        """
        Generate a CorrectionHint for the next retry.

        Args:
            task:         Planner task dict.
            error:        Error text from the previous failed attempt.
            file_content: Full text of the target file.
            attempt:      The attempt number that just failed (1-indexed).

        Returns:
            CorrectionHint ready to be injected via format_for_prompt().
        """
        cls = self.classify(error)
        recipe = _RECIPES[cls]

        focus_hint = recipe.focus_hint

        # For SEARCH_NOT_FOUND: enrich focus hint with candidate anchor lines
        if cls == ErrorClass.SEARCH_NOT_FOUND and file_content:
            candidates = _find_anchor_candidates(task.get("action", ""), file_content)
            if candidates:
                focus_hint = "Candidate anchor lines: " + " | ".join(candidates[:3])

        logger.debug(
            "[self_correction] attempt=%d error_class=%s approach=%s",
            attempt, cls.name, recipe.approach_name,
        )

        return CorrectionHint(
            error_class=cls,
            approach_name=recipe.approach_name,
            hint=recipe.hint,
            focus_hint=focus_hint,
        )

    def generate_critique(
        self,
        task: dict,
        error: str,
        error_class: ErrorClass,
        model_caller: Callable[[str], str],
    ) -> str:
        """
        Generate a verbal critique via a cheap LLM call.

        Args:
            task:         Planner task dict.
            error:        Raw error string from the failed attempt.
            error_class:  Classified ErrorClass (from self.classify()).
            model_caller: Callable that takes a prompt string and returns a
                          response string. Injected so tests can mock it without
                          real API calls.

        Returns:
            Critique string (1-3 sentences). Empty string on any failure —
            never raises so as not to block the retry loop.
        """
        try:
            prompt = _CRITIQUE_PROMPT_TEMPLATE.format(
                action      = str(task.get("action", ""))[:200],
                file_path   = str(task.get("file", "unknown")),
                error_class = error_class.name,
                error_msg   = str(error)[:300],
            )
            critique = model_caller(prompt)
            critique = (critique or "").strip()
            logger.debug(
                "[self_correction] critique generated: %d chars for %s/%s",
                len(critique), task.get("file", "?"), error_class.name,
            )
            return critique
        except Exception as exc:
            logger.warning("[self_correction] generate_critique failed: %s", exc)
            return ""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _find_anchor_candidates(action: str, file_content: str) -> list[str]:
    """
    Find function/class definition lines that are likely relevant to the action.
    Used to enrich SEARCH_NOT_FOUND hints with concrete anchor suggestions.
    """
    keywords = [w.lower() for w in re.findall(r"\w+", action) if len(w) > 3]
    candidates: list[str] = []
    for line in file_content.splitlines():
        stripped = line.strip()
        if re.match(r"(def |class )", stripped):
            if any(kw in stripped.lower() for kw in keywords):
                candidates.append(stripped[:80])
    return candidates
