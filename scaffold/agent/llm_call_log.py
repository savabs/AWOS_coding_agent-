"""
llm_call_log.py — one JSON line per model call, for the run health check.

Every model call AWOS makes (agent turns, planner, notebook, critique,
review, ...) appends one line to `.awos/llm_calls.jsonl` (relative to the
cwd, i.e. the arm's state dir under the job-series runner): which model was
asked for, which model answered, why the reply ended, token counts, cost and
how many visible characters came back. Failed calls are logged too, with
`error`. scripts/eval_health.py reads this file to catch silent failures —
truncated or empty replies, an unpinned model answering — that a solve rate
alone never shows.

Observability must not break execution: nothing here raises.

Env:
    AWOS_LLM_CALL_LOG=<path>  write here instead of .awos/llm_calls.jsonl
    AWOS_LLM_CALL_LOG=0       disable (also "off", "false", "")
Under pytest the log is off unless AWOS_LLM_CALL_LOG names a path, so tests
never write into the repo's own .awos/.
    AWOS_EVAL_ARM / AWOS_EVAL_JOB / AWOS_EVAL_REPEAT, when set by a runner,
    are copied into each line as arm / job / repeat.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

DEFAULT_PATH = Path(".awos") / "llm_calls.jsonl"
ENV_PATH = "AWOS_LLM_CALL_LOG"
_OFF = {"0", "off", "false", "no", ""}
_TAG_ENV = {"arm": "AWOS_EVAL_ARM", "job": "AWOS_EVAL_JOB", "repeat": "AWOS_EVAL_REPEAT"}


def log_path() -> Optional[Path]:
    """Where lines go, or None when logging is off."""
    value = os.environ.get(ENV_PATH)
    if value is None:
        if "PYTEST_CURRENT_TEST" in os.environ:
            return None
        return DEFAULT_PATH
    if value.strip().lower() in _OFF:
        return None
    return Path(value)


def _get(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _int(value: Any) -> Optional[int]:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _provider(response: Any) -> Optional[str]:
    """The serving provider OpenRouter names in its response (`provider`).

    The OpenAI SDK keeps unknown fields in `model_extra` (pydantic extras);
    a dict or plain object carries it as a key / attribute."""
    value = _get(response, "provider")
    if value is None and not isinstance(response, dict):
        extra = getattr(response, "model_extra", None)
        if isinstance(extra, dict):
            value = extra.get("provider")
    return value if isinstance(value, str) and value else None


def requested_provider() -> Optional[str]:
    """AWOS_OPENROUTER_PROVIDER as the run asked for it, or None (no pin)."""
    value = os.environ.get("AWOS_OPENROUTER_PROVIDER", "").strip()
    return value or None


def fields_from_response(response: Any) -> dict:
    """The loggable fields of an OpenAI- or Anthropic-shaped response.

    Keys: response_model, finish_reason, input_tokens, output_tokens,
    reasoning_tokens, cached_tokens, cost_usd (usage.cost when the provider
    reports it, e.g. OpenRouter), visible_chars, provider (OpenRouter's serving
    provider). Missing values are None.
    Never raises.
    """
    out: dict = dict(response_model=None, finish_reason=None, input_tokens=None,
                     output_tokens=None, reasoning_tokens=None, cached_tokens=None,
                     cost_usd=None, visible_chars=None, provider=None)
    try:
        out["response_model"] = _get(response, "model")
        out["provider"] = _provider(response)
        usage = _get(response, "usage")
        choices = _get(response, "choices")
        if choices:
            choice = choices[0]
            out["finish_reason"] = _get(choice, "finish_reason")
            content = _get(_get(choice, "message"), "content")
            out["visible_chars"] = len(content) if isinstance(content, str) else 0
            out["input_tokens"] = _int(_get(usage, "prompt_tokens"))
            out["output_tokens"] = _int(_get(usage, "completion_tokens"))
            out["cached_tokens"] = _int(_get(_get(usage, "prompt_tokens_details"), "cached_tokens"))
            out["reasoning_tokens"] = _int(
                _get(_get(usage, "completion_tokens_details"), "reasoning_tokens"))
        else:  # Anthropic Messages shape
            out["finish_reason"] = _get(response, "stop_reason")
            blocks = _get(response, "content")
            if isinstance(blocks, list):
                out["visible_chars"] = sum(
                    len(_get(b, "text") or "") for b in blocks if _get(b, "type") == "text")
            out["input_tokens"] = _int(_get(usage, "input_tokens"))
            out["output_tokens"] = _int(_get(usage, "output_tokens"))
            out["cached_tokens"] = _int(_get(usage, "cache_read_input_tokens"))
        cost = _get(usage, "cost")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            out["cost_usd"] = float(cost)
    except Exception:
        pass
    return out


def record_call(component: str, requested_model: Optional[str], response_model: Optional[str],
                finish_reason: Optional[str], input_tokens: Optional[int],
                output_tokens: Optional[int], reasoning_tokens: Optional[int] = None,
                cached_tokens: Optional[int] = None, cost_usd: Optional[float] = None,
                visible_chars: Optional[int] = None, error: Optional[str] = None,
                **extra: Any) -> None:
    """Append one call's line to the log. Never raises.

    `extra` holds optional fields such as attempt / final (utility retries)
    and tool_calls (agent turns).
    """
    try:
        path = log_path()
        if path is None:
            return
        line: dict = {
            "ts": round(time.time(), 3),
            "pid": os.getpid(),
            "component": component,
            "requested_model": requested_model,
            "response_model": response_model,
            "finish_reason": finish_reason,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "reasoning_tokens": reasoning_tokens,
            "cached_tokens": cached_tokens,
            "cost_usd": cost_usd,
            "visible_chars": visible_chars,
            "error": None if error is None else str(error)[:500],
        }
        for key, env in _TAG_ENV.items():
            if os.environ.get(env):
                line[key] = os.environ[env]
        line["provider"] = None
        line["requested_provider"] = requested_provider()
        line.update(extra)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, default=str) + "\n")
    except Exception:
        pass


def record_response(component: str, requested_model: Optional[str], response: Any,
                    **overrides: Any) -> None:
    """record_call with the fields taken from `response`; `overrides` win
    when not None (cost_usd given here is used only if usage.cost is absent)."""
    try:
        fields = fields_from_response(response)
        cost = overrides.pop("cost_usd", None)
        if fields["cost_usd"] is None:
            fields["cost_usd"] = cost
        for key, value in overrides.items():
            if value is not None or key not in fields:
                fields[key] = value
        record_call(component, requested_model, **fields)
    except Exception:
        pass


def record_error(component: str, requested_model: Optional[str], exc: BaseException,
                 **extra: Any) -> None:
    """Log a call that raised before any reply came back."""
    record_call(component, requested_model, None, None, None, None,
                error=f"{type(exc).__name__}: {exc}", **extra)
