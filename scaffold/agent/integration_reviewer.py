"""IntegrationReviewer: an advisory post-execution coherence check.

After all tasks of a goal complete, the reviewer reads the total diff and asks
a model to check cross-task coherence: interface consistency, broken call
chains, missing imports, stubs left behind. Its verdict is advisory: it is
printed and returned in the run result, never used to fail a goal.

Model: AWOS_REVIEW_MODEL, else the run's agent model (the one that did the
work, passed in by the orchestrator, or AWOS_AGENT_MODEL). No model known →
skipped; there is no premium default (it used to be a hardcoded Sonnet whose
spend was never recorded). The call goes through providers.utility_chat
(role "review": bounded reasoning, empty/cut-off replies refused, spend
recorded as request_type "integration_review", so it counts toward the goal
budget).

AWOS_INTEGRATION_REVIEW: auto (default) runs the review only when more than
one task executed (a cross-task check has nothing to cross-check after one;
ablation 2 measured ~47 s per job for an advisory verdict); 1 always runs it;
0 turns it off. AWOS_REVIEW_TIMEOUT_S (default 60) caps the call's wall-clock
time: in a baseline run a review call never returned and held two jobs past
the execution summary for 754 s and 545 s, until the job timeout.

A skipped review returns {"passed": True, "skipped": True, "skip_reason": ...}.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
from typing import Any, Optional

try:
    from .providers import ModelReplyError, chat_client, utility_chat
except ImportError:
    from providers import ModelReplyError, chat_client, utility_chat

REVIEW_MODEL_ENV = "AWOS_REVIEW_MODEL"
REVIEW_SWITCH_ENV = "AWOS_INTEGRATION_REVIEW"
REVIEW_TIMEOUT_ENV = "AWOS_REVIEW_TIMEOUT_S"
DEFAULT_REVIEW_TIMEOUT_S = 60.0
REQUEST_TYPE = "integration_review"
MAX_OUTPUT_TOKENS = 4096

#: Bullet text that says "nothing here" rather than naming an issue.
_NOISE = re.compile(
    r"(none|nil|nothing|n/?a|not applicable|"
    r"none (found|identified|detected|noted)|"
    r"no (issues?|problems?|suggestions?|concerns?)( (found|identified|detected|noted))?)",
    re.I,
)


def review_mode() -> str:
    """AWOS_INTEGRATION_REVIEW: "auto" (default), "always" (1/true/yes/on) or "off"."""
    value = os.getenv(REVIEW_SWITCH_ENV, "auto").strip().lower()
    if value in ("0", "false", "no", "off"):
        return "off"
    if value in ("1", "true", "yes", "on", "always"):
        return "always"
    return "auto"


def enabled() -> bool:
    return review_mode() != "off"


def review_timeout_s() -> float:
    """AWOS_REVIEW_TIMEOUT_S, default 60; a bad or non-positive value falls back."""
    try:
        value = float(os.getenv(REVIEW_TIMEOUT_ENV, DEFAULT_REVIEW_TIMEOUT_S))
    except ValueError:
        return DEFAULT_REVIEW_TIMEOUT_S
    return value if value > 0 else DEFAULT_REVIEW_TIMEOUT_S


class ReviewTimeout(Exception):
    """The review call did not finish within AWOS_REVIEW_TIMEOUT_S."""


def _call_with_deadline(fn, timeout_s: float):
    """
    fn() on a daemon thread: its result, its exception re-raised, or
    ReviewTimeout after `timeout_s`. The per-request HTTP timeout alone did
    not bound the baseline hang, so the wall clock is enforced here; an
    abandoned call cannot keep the process alive (daemon thread).
    """
    box: dict = {}

    def run():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 — handed to the caller
            box["error"] = exc

    worker = threading.Thread(target=run, name="integration-review", daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        raise ReviewTimeout(f"no reply within {timeout_s:g}s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def review_model(agent_model: Optional[str] = None) -> Optional[str]:
    """AWOS_REVIEW_MODEL, else the run's agent model, else AWOS_AGENT_MODEL."""
    for value in (os.getenv(REVIEW_MODEL_ENV), agent_model, os.getenv("AWOS_AGENT_MODEL")):
        if value and value.strip():
            return value.strip()
    return None


def _is_cheap_only() -> bool:
    try:
        from .escalation_engine import is_cheap_only
    except ImportError:
        try:
            from escalation_engine import is_cheap_only
        except ImportError:
            return False
    return is_cheap_only()


def _skipped(reason: str) -> dict[str, Any]:
    return {"passed": True, "issues": [], "suggestions": [], "raw_response": "",
            "skipped": True, "skip_reason": reason}


class IntegrationReviewer:
    """Reviews the total diff for cross-task coherence (advisory)."""

    def __init__(self, api_key: str | None = None, *, client: Any = None,
                 model: Optional[str] = None, tracker: Any = None) -> None:
        # api_key is kept for callers of the old signature; the reviewer now
        # goes through the OpenRouter chat client.
        self.api_key = api_key
        self.client = client
        self.model = model
        self.tracker = tracker

    # ── Public API ─────────────────────────────────────────────────────────

    def review(self, codebase_root: str, execution_log: list[dict], *,
               agent_model: Optional[str] = None, tracker: Any = None,
               n_tasks: Optional[int] = None) -> dict[str, Any]:
        """Review the total diff after task execution.

        `n_tasks` is how many tasks the goal executed; in auto mode one task
        (or none) skips the review. None means unknown: the review runs.

        Returns {"passed", "issues", "suggestions", "raw_response",
        "model_used"} or, when not run, {"passed": True, "skipped": True,
        "skip_reason": str, ...}. A model failure or timeout is a skip, never
        an exception.
        """
        mode = review_mode()
        if mode == "off":
            return _skipped(f"{REVIEW_SWITCH_ENV}=0")
        if mode == "auto" and n_tasks is not None and n_tasks <= 1:
            return _skipped("single task")
        diff = self._get_diff(codebase_root)
        if not diff:
            return _skipped("no diff to review")
        model = self.model or review_model(agent_model)
        if not model:
            reason = f"no review model ({REVIEW_MODEL_ENV} and AWOS_AGENT_MODEL unset)"
            if _is_cheap_only():
                reason = "cheap-only: no premium default reviewer; " + reason
            return _skipped(reason)
        client = self.client if self.client is not None else chat_client()
        if client is None:
            return _skipped("no model client (OPENROUTER_API_KEY not set)")

        prompt = self._build_prompt(diff, execution_log)
        timeout_s = review_timeout_s()
        try:
            # One SDK attempt with the cap as its HTTP timeout, and the same
            # cap on the wall clock around the whole call (utility_chat's
            # empty-reply retry included), so nothing can multiply it.
            text, _info = _call_with_deadline(
                lambda: utility_chat(
                    client, "review", model,
                    [{"role": "user", "content": prompt}],
                    max_tokens=MAX_OUTPUT_TOKENS,
                    tracker=tracker if tracker is not None else self.tracker,
                    request_type=REQUEST_TYPE,
                    sdk_retries=0,
                    timeout=timeout_s,
                ),
                timeout_s,
            )
        except ReviewTimeout:
            return _skipped("timeout")
        except ModelReplyError as exc:
            return _skipped(f"unusable review reply: {exc}")
        except Exception as exc:
            return _skipped(f"review call failed: {type(exc).__name__}: {exc}"[:300])
        return self._parse_response(text, model_used=model)

    # ── Internal ─────────────────────────────────────────────────────────────

    def _get_diff(self, codebase_root: str) -> str:
        """Get git diff of uncommitted changes."""
        try:
            result = subprocess.run(
                ["git", "diff", "--no-color"],
                cwd=codebase_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout
        except (subprocess.SubprocessError, FileNotFoundError):
            return ""

    def _build_prompt(self, diff: str, execution_log: list[dict]) -> str:
        tasks_summary = "\n".join(
            f"- Task {log.get('task_id')}: {log.get('status')} — {log.get('reason', '')}"
            for log in execution_log
        )
        return f"""You are a senior staff engineer reviewing a pull request that was generated by an AI agent executing multiple isolated tasks.

Your job is to check for CROSS-TASK INTEGRATION ISSUES that cheap models miss:
1. Missing imports (a task added a function but forgot to import it)
2. Interface mismatches (task A expects X, task B provides Y)
3. Broken call chains (function renamed in one task, not updated in others)
4. Missing error handling / edge cases
5. Inconsistent patterns (mix of old and new style)
6. TODOs or stubs left unimplemented

TASKS EXECUTED:
{tasks_summary}

GIT DIFF:
```diff
{diff[:8000]}
```

Respond in this exact format (leave a list empty when there is nothing):
VERDICT: PASS | NEEDS_FIX
ISSUES:
- <issue 1>
SUGGESTIONS:
- <suggestion 1>
"""

    @staticmethod
    def _is_noise(item: str) -> bool:
        return bool(_NOISE.fullmatch(item.strip().strip(".!:;-*` ").strip()))

    def _parse_response(self, text: str, model_used: str = "") -> dict[str, Any]:
        verdict = None
        issues: list[str] = []
        suggestions: list[str] = []
        section = None
        for line in (text or "").splitlines():
            stripped = line.strip()
            upper = stripped.replace("*", "").strip().upper()  # "**VERDICT:**" too
            if upper.startswith("VERDICT:"):
                value = upper.split(":", 1)[1]
                verdict = "NEEDS_FIX" if "NEEDS_FIX" in value else ("PASS" if "PASS" in value else verdict)
                section = None
                continue
            if upper.startswith("ISSUES:"):
                section = issues
                continue
            if upper.startswith("SUGGESTIONS:"):
                section = suggestions
                continue
            if section is not None and stripped.startswith(("-", "*")):
                item = stripped[1:].strip()
                if item and not self._is_noise(item):
                    section.append(item)

        if verdict is None:
            passed = "PASS" in (text or "") and "NEEDS_FIX" not in (text or "")
        else:
            passed = verdict == "PASS"
        return {
            "passed": passed,
            "issues": issues,
            "suggestions": suggestions,
            "raw_response": text,
            "model_used": model_used,
        }
