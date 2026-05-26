"""
critic_engine.py — P7-A Generator-Critic Self-Play.

The Critic is a separate LLM pass that reviews a generated patch *before*
it is written to disk.  It plays the role of a code reviewer: finds logic
gaps, missing edge cases, and interface breakages.

Self-play loop (orchestrator drives):
    Generator  →  patch_v1
    Critic     →  CritiqueResult(verdict="revise", issues=[...], hints=...)
    Generator  →  patch_v2  (uses hints as error_context)
    Critic     →  CritiqueResult(verdict="approve", confidence=0.87)
    PRM        →  score(patch_v1), score(patch_v2) → pick best

Design principles:
  1. Critic is ALWAYS cheaper than the generator (Gemini Flash or DeepSeek).
  2. Max rounds is bounded (default 1, env AWOS_CRITIC_ROUNDS=N).
  3. Gate: skip critic for trivial tasks (complexity=low, attempt=1, no errors).
  4. Graceful degradation: if critic fails for any reason, return approval.
  5. Never blocks — always returns a CritiqueResult.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

# ── Env config ────────────────────────────────────────────────────────────────
_MAX_CRITIC_ROUNDS = int(os.getenv("AWOS_CRITIC_ROUNDS", "1"))
_CRITIC_ENABLED    = os.getenv("AWOS_CRITIC", "true").lower() != "false"
_CRITIC_MODEL      = os.getenv("AWOS_CRITIC_MODEL", "deepseek-chat")   # cheap by default


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class CritiqueResult:
    """
    Output of one critic pass.

    verdict:    "approve" | "revise"
    confidence: 0.0–1.0 — how confident the critic is that the patch is correct
    issues:     list of specific problems found (empty when approved)
    hints:      brief guidance for the generator if verdict=="revise"
    cost_usd:   cost of the critic call
    model_used: model that ran the critic
    """
    verdict: str                           # "approve" | "revise"
    confidence: float                      # 0.0–1.0
    issues: List[str] = field(default_factory=list)
    hints: str = ""
    cost_usd: float = 0.0
    model_used: str = ""
    raw_response: str = ""

    @property
    def should_revise(self) -> bool:
        return self.verdict == "revise" and bool(self.issues)

    @property
    def error_context_for_generator(self) -> str:
        """Formatted string to inject into the generator's retry prompt."""
        if not self.issues:
            return ""
        parts = ["CRITIC REVIEW — fix these issues:"]
        for i, issue in enumerate(self.issues[:5], 1):
            parts.append(f"  {i}. {issue}")
        if self.hints:
            parts.append(f"\nHINT: {self.hints}")
        return "\n".join(parts)


# ── Critic Engine ─────────────────────────────────────────────────────────────

class CriticEngine:
    """
    Reviews a generated patch before it is applied.

    Usage:
        critic = CriticEngine()
        result = critic.critique(
            task=task,
            patch=search_replace_dict,
            file_content=original_file_content,
            tracker=tracker,
        )
        if result.should_revise:
            # re-run generator with result.error_context_for_generator
    """

    # Critic system prompt — structured JSON output
    _SYSTEM = (
        "You are an expert code reviewer. Your job is to catch bugs BEFORE they are committed. "
        "Be concise and precise. Focus only on real problems, not style."
    )

    _PROMPT_TEMPLATE = """\
Review this proposed code change for correctness.

TASK: {action}
FILE: {file_path}

ORIGINAL CODE (relevant section):
```
{context}
```

PROPOSED CHANGE:
SEARCH (text being replaced):
```
{search}
```
REPLACE (new text):
```
{replace}
```

Review for:
1. Logic correctness — does this actually accomplish the task?
2. Edge cases — inputs or states that could cause failures
3. Breaking changes — interfaces/contracts other code relies on
4. Missing error handling — uncaught exceptions, None dereferences
5. Test alignment — will existing tests still pass after this change?

If the patch looks correct, approve it.
If there are real issues, list them and provide a concrete improvement hint.

OUTPUT: JSON only, no prose outside the JSON.
```json
{{"verdict": "approve" | "revise", "confidence": 0.0-1.0, "issues": ["..."], "hints": "..."}}
```
"""

    def __init__(
        self,
        deepseek_key: Optional[str] = None,
        anthropic_key: Optional[str] = None,
        google_key: Optional[str] = None,
    ) -> None:
        self._deepseek_key  = deepseek_key  or os.getenv("DEEPSEEK_API_KEY")
        self._anthropic_key = anthropic_key or os.getenv("ANTHROPIC_API_KEY")
        self._google_key    = google_key    or os.getenv("GOOGLE_API_KEY")
        self._client        = None
        self._anthropic     = None
        self._google        = None
        self._init_clients()

    def _init_clients(self) -> None:
        if self._deepseek_key:
            try:
                from openai import OpenAI
                self._client = OpenAI(
                    api_key=self._deepseek_key,
                    base_url="https://api.deepseek.com",
                )
            except Exception as e:
                logger.debug("[critic] DeepSeek client init failed: %s", e)

        if self._anthropic_key:
            try:
                from anthropic import Anthropic
                self._anthropic = Anthropic(api_key=self._anthropic_key)
            except Exception as e:
                logger.debug("[critic] Anthropic client init failed: %s", e)

    # ── Public API ────────────────────────────────────────────────────────────

    def should_run(self, task: dict, attempt: int) -> bool:
        """
        Gate: decide whether to run the critic for this task.

        Skip if:
          - AWOS_CRITIC=false
          - complexity == "low" AND attempt == 1 AND no error_context
          - max_rounds == 0
        """
        if not _CRITIC_ENABLED:
            return False
        if _MAX_CRITIC_ROUNDS == 0:
            return False
        complexity = str(task.get("complexity", "medium")).lower()
        error_ctx  = task.get("error_context", "")
        if complexity == "low" and attempt == 1 and not error_ctx:
            return False
        return True

    def critique(
        self,
        task: dict,
        patch: dict,
        file_content: str,
        tracker=None,
        model_spec=None,
    ) -> CritiqueResult:
        """
        Run one critic pass on a generated patch.

        Args:
            task:         AWOS task dict (action, file, complexity)
            patch:        worker result dict (search, replace, reasoning)
            file_content: original file text before the patch
            tracker:      optional TokenTracker for cost accounting
            model_spec:   optional ModelSpec (used only for context, critic always uses cheap model)

        Returns:
            CritiqueResult — always succeeds (degrades gracefully on API errors)
        """
        if not patch.get("success") or not patch.get("search"):
            return CritiqueResult(verdict="approve", confidence=1.0,
                                  model_used="skipped:no_patch")

        action     = task.get("action", "")
        file_path  = task.get("file", "")
        search_str = patch.get("search", "")
        replace_str = patch.get("replace", "")

        context = self._extract_context(file_content, search_str)
        prompt  = self._PROMPT_TEMPLATE.format(
            action=action,
            file_path=file_path,
            context=context[:3000],
            search=search_str[:1500],
            replace=replace_str[:1500],
        )

        try:
            raw, model_used, cost = self._call_llm(prompt, tracker)
            result = self._parse_response(raw, model_used, cost)
            logger.info(
                "[critic] verdict=%s confidence=%.2f issues=%d model=%s",
                result.verdict, result.confidence, len(result.issues), model_used,
            )
            return result
        except Exception as exc:
            logger.warning("[critic] critique failed (approve by default): %s", exc)
            return CritiqueResult(verdict="approve", confidence=0.5,
                                  model_used="error:fallback",
                                  issues=[], hints=str(exc)[:100])

    # ── Private: LLM call ─────────────────────────────────────────────────────

    def _call_llm(
        self,
        prompt: str,
        tracker=None,
    ) -> tuple[str, str, float]:
        """Returns (response_text, model_name, cost_usd)."""

        # Prefer DeepSeek (cheap critic)
        if self._client:
            return self._call_deepseek(prompt, tracker)

        # Fallback to Haiku (still cheap critic)
        if self._anthropic:
            return self._call_haiku(prompt, tracker)

        raise RuntimeError("[critic] no API client available")

    def _call_deepseek(
        self,
        prompt: str,
        tracker=None,
    ) -> tuple[str, str, float]:
        model = _CRITIC_MODEL
        resp = self._client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": self._SYSTEM},
                {"role": "user",   "content": prompt},
            ],
            max_tokens=512,
            temperature=0.0,
        )
        text = resp.choices[0].message.content or ""
        inp  = resp.usage.prompt_tokens
        out  = resp.usage.completion_tokens
        cost = (inp / 1_000_000) * 0.14 + (out / 1_000_000) * 0.28
        if tracker:
            tracker.record("critic", "DeepSeek Critic", inp, out, cost)
        return text, f"DeepSeek({model})", cost

    def _call_haiku(
        self,
        prompt: str,
        tracker=None,
    ) -> tuple[str, str, float]:
        model = "claude-haiku-4-5"
        resp = self._anthropic.messages.create(
            model=model,
            max_tokens=512,
            system=self._SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text or ""
        inp  = resp.usage.input_tokens
        out  = resp.usage.output_tokens
        cost = (inp / 1_000_000) * 1.00 + (out / 1_000_000) * 5.00
        if tracker:
            tracker.record("critic", "Haiku Critic", inp, out, cost)
        return text, f"Haiku({model})", cost

    # ── Private: Response parsing ─────────────────────────────────────────────

    def _parse_response(
        self,
        raw: str,
        model_used: str,
        cost: float,
    ) -> CritiqueResult:
        """Parse JSON from LLM response; degrade gracefully on malformed output."""
        text = raw.strip()

        # Strip markdown fences
        fence = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if fence:
            text = fence.group(1)
        else:
            brace = re.search(r'\{.*\}', text, re.DOTALL)
            if brace:
                text = brace.group(0)

        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            # If we can't parse JSON, treat as approval with low confidence
            logger.debug("[critic] JSON parse failed for: %s", raw[:120])
            return CritiqueResult(
                verdict="approve", confidence=0.4,
                model_used=model_used, cost_usd=cost,
                raw_response=raw,
            )

        verdict    = str(data.get("verdict", "approve")).lower()
        confidence = float(data.get("confidence", 0.5))
        issues     = [str(x) for x in data.get("issues", []) if x]
        hints      = str(data.get("hints", ""))

        if verdict not in ("approve", "revise"):
            verdict = "approve"

        # Auto-downgrade to approve if no issues despite "revise" verdict
        if verdict == "revise" and not issues:
            verdict = "approve"

        return CritiqueResult(
            verdict=verdict,
            confidence=max(0.0, min(1.0, confidence)),
            issues=issues,
            hints=hints,
            cost_usd=cost,
            model_used=model_used,
            raw_response=raw,
        )

    # ── Private: Context extraction ───────────────────────────────────────────

    def _extract_context(self, file_content: str, search_str: str) -> str:
        """Return file context around the search string (±30 lines)."""
        if not search_str or not file_content:
            return file_content[:2000]

        lines    = file_content.splitlines()
        idx      = file_content.find(search_str)
        if idx == -1:
            return "\n".join(lines[:60])

        line_no = file_content[:idx].count("\n")
        start   = max(0, line_no - 30)
        end     = min(len(lines), line_no + len(search_str.splitlines()) + 30)
        return "\n".join(lines[start:end])


# ── Helpers for orchestrator ──────────────────────────────────────────────────

def max_critic_rounds() -> int:
    """Returns the configured maximum critic rounds (0 = critic disabled)."""
    return _MAX_CRITIC_ROUNDS if _CRITIC_ENABLED else 0
