"""
claude_compat.py — make AWOS's OpenAI-shaped utility calls work on Claude.

Two things broke every non-agent-loop component on the direct Anthropic path
(AWOS_AGENT_MODEL=claude-haiku-5-5, no OPENROUTER_API_KEY), see
docs/memory/runs/awos_vs_aider_haiku_2026-10.md:

1. The planner, acceptance generator, one-shot, arbiter, integration review
   and notebook all call `client.chat.completions.create(...)` on an
   OpenAI-shaped client. Without OpenRouter there was no such client for a
   Claude model: CheapPlanner sent claude-haiku-5-5 to the OpenCode endpoint
   (HTTP 400 MissingSessionID), and one-shot / acceptance found no client at
   all ("gate inactive: no OpenAI-shaped client configured").
2. Claude 5.x (and Opus 4.7/4.8) reject non-default sampling parameters:
   `temperature`, `top_p`, `top_k` -> HTTP 400. Every utility call sent
   temperature=0 or 0.3.

This module holds the shared fix:

- `claude_rejects_sampling(model)` / `sampling_kwargs(model, temperature)`:
  the one rule for which models must not get sampling params, used by
  utility_chat, CheapPlanner and the adapter below (OpenRouter forwards the
  same params to Anthropic, so the rule applies there too).
- `AnthropicChatClient`: the OpenAI chat-completions surface over the direct
  Anthropic Messages API. It converts system messages, maps `reasoning`
  effort to `output_config.effort`, adds prompt-cache breakpoints (system +
  last message, AWOS_ANTHROPIC_CACHE), drops OpenRouter-only fields, and
  returns an OpenAI-shaped response whose usage carries cache counts, so the
  existing spend recording (providers._record_utility_spend) prices it
  cache-aware and writes it to the ledger.
"""
from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any, Optional

#: claude-<family>-<major>[-<minor>]...; old ids (claude-3-5-sonnet-...) have no family.
_CLAUDE_ID = re.compile(
    r"^claude-(?:(?P<family>[a-z]+)-)?(?P<major>\d+)(?:[-.](?P<minor>\d{1,2})(?!\d))?")

#: Fields only OpenRouter (or a local server) understands; never sent to Anthropic.
_OPENROUTER_ONLY = ("reasoning", "provider", "session_id", "grammar", "transforms",
                    "models", "route", "usage")
_SAMPLING = ("temperature", "top_p", "top_k")


def bare_model_id(model: Optional[str]) -> str:
    """'anthropic/claude-haiku-5.5' -> 'claude-haiku-5-5'; other ids unchanged (lower-cased)."""
    m = (model or "").strip().lower()
    if "/" in m:
        m = m.rsplit("/", 1)[-1]
    if m.startswith("claude-"):
        m = re.sub(r"(\d)\.(\d)", r"\1-\2", m)
    return m


def is_claude(model: Optional[str]) -> bool:
    return bare_model_id(model).startswith("claude-")


def claude_version(model: Optional[str]) -> Optional[tuple[str, int, int]]:
    """(family, major, minor) for a Claude id, else None. ('', 3, 5) for old ids."""
    m = _CLAUDE_ID.match(bare_model_id(model))
    if not m:
        return None
    return (m.group("family") or "", int(m.group("major")), int(m.group("minor") or 0))


def claude_rejects_sampling(model: Optional[str]) -> bool:
    """
    True when the model 400s on non-default temperature/top_p/top_k: every
    Claude 5.x model (Haiku/Sonnet/Opus 5.x, Fable, Mythos) and Opus 4.7/4.8.
    Claude 4.6 and older still accept them.
    """
    v = claude_version(model)
    if v is None:
        return False
    family, major, minor = v
    if major >= 5 or family in ("fable", "mythos"):
        return True
    return family == "opus" and major == 4 and minor >= 7


def claude_supports_effort(model: Optional[str]) -> bool:
    """`output_config.effort` is accepted on Claude 4.6+ (not Haiku 4.5 / older)."""
    v = claude_version(model)
    if v is None:
        return False
    family, major, minor = v
    if major >= 5 or family in ("fable", "mythos"):
        return True
    return family in ("opus", "sonnet") and major == 4 and minor >= 6


def sampling_kwargs(model: Optional[str], temperature: Optional[float] = None,
                    **others: Any) -> dict:
    """The sampling kwargs a request for `model` may carry: {} for a model that
    rejects them, else the non-None ones given."""
    if claude_rejects_sampling(model):
        return {}
    out = {"temperature": temperature, **others}
    return {k: v for k, v in out.items() if v is not None}


def strip_rejected_params(model: Optional[str], kwargs: dict) -> dict:
    """A copy of request `kwargs` without the sampling params `model` rejects."""
    if not claude_rejects_sampling(model):
        return dict(kwargs)
    return {k: v for k, v in kwargs.items() if k not in _SAMPLING}


# ── OpenAI chat surface over the Anthropic Messages API ──────────────────────

_EFFORTS = ("low", "medium", "high", "xhigh", "max")
_FINISH = {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length",
           "tool_use": "tool_calls", "refusal": "content_filter", "pause_turn": "stop"}
DEFAULT_MAX_TOKENS = 4096


def _effort_from_reasoning(reasoning: Any) -> Optional[str]:
    """OpenRouter `reasoning` -> Anthropic effort: {"effort": "low"} -> "low";
    "minimal" and {"enabled": False} -> "low" (Claude 5.x thinking cannot be
    fully disabled at every effort, low is the cheap setting)."""
    if not isinstance(reasoning, dict):
        return None
    if reasoning.get("enabled") is False:
        return "low"
    effort = str(reasoning.get("effort") or "").lower()
    if effort == "minimal":
        return "low"
    return effort if effort in _EFFORTS else None


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(p.get("text", "")) for p in content
                       if isinstance(p, dict) and p.get("type", "text") == "text")
    return "" if content is None else str(content)


def _convert_messages(messages: list) -> tuple[str, list]:
    """OpenAI messages -> (system text, Anthropic messages). Consecutive
    same-role turns are merged; a leading assistant turn gets a user stub."""
    system_parts: list[str] = []
    out: list[dict] = []
    for msg in messages or []:
        role = msg.get("role") if isinstance(msg, dict) else None
        text = _text_of(msg.get("content") if isinstance(msg, dict) else msg)
        if role in ("system", "developer"):
            if text:
                system_parts.append(text)
            continue
        role = "assistant" if role == "assistant" else "user"
        if not text:
            continue
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n\n" + text
        else:
            out.append({"role": role, "content": text})
    if out and out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": "Continue."})
    if not out:
        out.append({"role": "user", "content": "Continue."})
    return "\n\n".join(system_parts), out


def _usage_shape(usage: Any) -> SimpleNamespace:
    """Anthropic usage -> OpenAI usage (prompt_tokens = the whole prompt)."""
    def _n(name: str) -> int:
        try:
            return max(0, int(getattr(usage, name, 0) or 0))
        except (TypeError, ValueError):
            return 0
    read, write = _n("cache_read_input_tokens"), _n("cache_creation_input_tokens")
    prompt = _n("input_tokens") + read + write
    completion = _n("output_tokens")
    return SimpleNamespace(
        prompt_tokens=prompt, completion_tokens=completion,
        total_tokens=prompt + completion,
        prompt_tokens_details=SimpleNamespace(cached_tokens=read, cache_write_tokens=write),
    )


def message_text(response: Any) -> str:
    """The text of an Anthropic Message: its text blocks joined. Claude 5.x
    replies may open with a thinking block, so content[0].text is unsafe."""
    return "".join(getattr(b, "text", "") or "" for b in (getattr(response, "content", None) or [])
                   if getattr(b, "type", None) == "text")


def to_openai_response(response: Any, model: str) -> SimpleNamespace:
    """An Anthropic Message as an OpenAI chat completion (text blocks only;
    thinking blocks are dropped)."""
    text = message_text(response)
    stop = getattr(response, "stop_reason", None)
    message = SimpleNamespace(role="assistant", content=text, tool_calls=None)
    choice = SimpleNamespace(index=0, message=message,
                             finish_reason=_FINISH.get(stop, stop or "stop"))
    return SimpleNamespace(
        id=getattr(response, "id", ""), object="chat.completion",
        model=getattr(response, "model", None) or model,
        choices=[choice], usage=_usage_shape(getattr(response, "usage", None)),
        stop_reason=stop, provider="anthropic",
    )


class AnthropicChatClient:
    """
    `client.chat.completions.create(model=..., messages=..., ...)` served by
    the direct Anthropic Messages API, for AWOS's utility callers (planner,
    acceptance, one-shot, arbiter, integration review, notebook, critique).
    Claude models only; streaming is refused with TypeError so loop_guard
    falls back to a plain call.
    """

    provider = "anthropic"

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.chat = self
        self.completions = self

    def with_options(self, **kwargs: Any) -> "AnthropicChatClient":
        return AnthropicChatClient(self._inner.with_options(**kwargs))

    def build_request(self, *, model: str, messages: list, max_tokens: Optional[int] = None,
                      **kwargs: Any) -> dict:
        """The Anthropic request for an OpenAI-style call (no network)."""
        if not is_claude(model):
            raise ValueError(
                f"model {model!r} is not a Claude model; the direct Anthropic client "
                "serves claude-* only (set OPENROUTER_API_KEY or a direct key for others)")
        wire = bare_model_id(model)
        system, conv = _convert_messages(messages)
        max_tokens = kwargs.pop("max_completion_tokens", None) or max_tokens
        req: dict = {"model": wire, "max_tokens": int(max_tokens or DEFAULT_MAX_TOKENS),
                     "messages": conv}
        if system:
            req["system"] = system
        req.update(sampling_kwargs(wire, kwargs.pop("temperature", None),
                                   top_p=kwargs.pop("top_p", None)))
        body = dict(kwargs.pop("extra_body", None) or {})
        effort = _effort_from_reasoning(body.pop("reasoning", None) or kwargs.pop("reasoning", None))
        if effort and claude_supports_effort(wire):
            req["output_config"] = {"effort": effort}
        for key in _OPENROUTER_ONLY:
            body.pop(key, None)
            kwargs.pop(key, None)
        stop = kwargs.pop("stop", None)
        if stop:
            req["stop_sequences"] = [stop] if isinstance(stop, str) else list(stop)
        for key in ("timeout", "extra_headers"):
            if kwargs.get(key) is not None:
                req[key] = kwargs.pop(key)
        if body:
            req["extra_body"] = body
        # Everything else (response_format, n, seed, presence_penalty, ...) has no
        # Anthropic equivalent on this path and is dropped.
        try:
            from .agent_loop import with_cache_breakpoints
        except ImportError:
            from agent_loop import with_cache_breakpoints
        sys_c, msgs_c, _ = with_cache_breakpoints(req.get("system"), req["messages"], [])
        if sys_c:
            req["system"] = sys_c
        req["messages"] = msgs_c
        return req

    def create(self, *, model: str, messages: list, stream: bool = False, **kwargs: Any) -> Any:
        if stream:
            raise TypeError("AnthropicChatClient does not stream")
        kwargs.pop("stream_options", None)
        req = self.build_request(model=model, messages=messages, **kwargs)
        response = self._inner.messages.create(**req)
        return to_openai_response(response, req["model"])


def is_anthropic_chat_client(client: Any) -> bool:
    return isinstance(client, AnthropicChatClient)
