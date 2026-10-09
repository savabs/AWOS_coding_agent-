"""
providers.py — one place that decides where a model call goes.

With OPENROUTER_API_KEY set, every client routes through OpenRouter and direct
provider keys are ignored: one key, one balance, one way to fail. Without it,
callers fall back to the direct provider they name.

AWOS_PROVIDER=local (or AWOS_AGENT_MODEL=local/<name>) overrides both: every
client talks to one OpenAI-compatible local server (llama-server, see
scripts/local_model.sh) at AWOS_LOCAL_BASE_URL, no key, $0 cost. Whatever model
a caller asks for is served by the one loaded model, AWOS_LOCAL_MODEL.

See docs/specs/openrouter_only_spec.md and docs/specs/local_provider_spec.md.
"""
from __future__ import annotations

import os
import re
from types import SimpleNamespace
from typing import Any, Optional

from anthropic import Anthropic
from openai import OpenAI

try:  # one log line per model call, for scripts/eval_health.py
    from .llm_call_log import record_error, record_response
except ImportError:
    from llm_call_log import record_error, record_response

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
#: The Anthropic SDK appends /v1/messages itself.
OPENROUTER_ANTHROPIC_BASE_URL = "https://openrouter.ai/api"

#: Bare-id prefix → OpenRouter vendor namespace.
_VENDORS = (
    ("claude-", "anthropic"),
    ("deepseek-", "deepseek"),
    ("gpt-", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
    ("gemini-", "google"),
    ("qwen", "qwen"),
    ("kimi-", "moonshotai"),
    ("glm-", "z-ai"),
    ("minimax-", "minimax"),
)


#: Per-request model timeout and SDK retries. The SDK defaults (600 s, 2
#: retries) let one hung call stall a job for half an hour; a live run had an
#: agent-loop call hang 12+ minutes. A timed-out call raises, which AgentLoop
#: turns into a clean `model_error` stop.
DEFAULT_MODEL_TIMEOUT_S = 120.0
DEFAULT_MODEL_MAX_RETRIES = 2


def model_timeout_s() -> float:
    """AWOS_MODEL_TIMEOUT_S, default 120; a bad or non-positive value falls back."""
    try:
        value = float(os.getenv("AWOS_MODEL_TIMEOUT_S", DEFAULT_MODEL_TIMEOUT_S))
    except ValueError:
        return DEFAULT_MODEL_TIMEOUT_S
    return value if value > 0 else DEFAULT_MODEL_TIMEOUT_S


def model_max_retries() -> int:
    """AWOS_MODEL_MAX_RETRIES, default 2; a bad or negative value falls back."""
    try:
        value = int(os.getenv("AWOS_MODEL_MAX_RETRIES", DEFAULT_MODEL_MAX_RETRIES))
    except ValueError:
        return DEFAULT_MODEL_MAX_RETRIES
    return value if value >= 0 else DEFAULT_MODEL_MAX_RETRIES


def client_options() -> dict:
    """Keyword arguments every OpenAI/Anthropic client AWOS builds should get."""
    return {"timeout": model_timeout_s(), "max_retries": model_max_retries()}


#: Pins the model of every planner LLM call (CheapPlanner primary and
#: fallback, and the Sonnet Planner). Unset, each planner keeps its own default.
PLANNER_MODEL_ENV = "AWOS_PLANNER_MODEL"


def planner_model(default: str) -> str:
    """AWOS_PLANNER_MODEL when set (non-blank), else the planner's `default`."""
    return os.getenv(PLANNER_MODEL_ENV, "").strip() or default


#: How hard a reasoning model may think during a planner call (OpenRouter's
#: `reasoning` request field). DeepSeek V4 Flash left unbounded spent its
#: whole 4096-token budget on hidden reasoning and returned "" after 2–3
#: minutes on half the ordertool jobs; at "low" the same plan took ~730
#: reasoning tokens. Values: minimal | low (default) | medium | high |
#: off (no reasoning) | default (send nothing; the model's own setting).
#: AWOS_PLANNER_REASONING is the planner's older name for
#: AWOS_REASONING_PLANNER; the new name wins when both are set.
PLANNER_REASONING_ENV = "AWOS_PLANNER_REASONING"
DEFAULT_PLANNER_REASONING = "low"
#: Per-role reasoning effort: AWOS_REASONING_<ROLE> (e.g. AWOS_REASONING_NOTEBOOK).
REASONING_ENV_PREFIX = "AWOS_REASONING_"
DEFAULT_REASONING = "low"
_REASONING_EFFORTS = ("minimal", "low", "medium", "high")
#: Reasoning switched off — the planner's retry after an empty reply.
REASONING_OFF: dict = {"enabled": False}


def _reasoning_value(value: str) -> Optional[dict]:
    value = value.strip().lower() or DEFAULT_REASONING
    if value in ("default", "model"):
        return None
    if value in ("off", "none", "0", "false", "disabled"):
        return dict(REASONING_OFF)
    if value not in _REASONING_EFFORTS:
        value = DEFAULT_REASONING
    return {"effort": value}


def reasoning_for(role: str) -> Optional[dict]:
    """
    The `reasoning` field for a call made in `role` ("planner", "notebook",
    "review", "critique", ...), or None to send none. AWOS_REASONING_<ROLE>,
    default "low"; the planner also honours AWOS_PLANNER_REASONING.
    """
    key = re.sub(r"[^A-Z0-9]", "_", (role or "").upper())
    value = os.getenv(REASONING_ENV_PREFIX + key, "").strip()
    if not value and key == "PLANNER":
        value = os.getenv(PLANNER_REASONING_ENV, "").strip()
    return _reasoning_value(value)


def planner_reasoning() -> Optional[dict]:
    """The `reasoning` field for planner calls, or None to send none."""
    return reasoning_for("planner")


#: OpenRouter provider pin (cost ablation). OpenRouter load-balances one model
#: across many third-party endpoints, so a prompt prefix lands on a different
#: cache each turn and prompt caching never hits. Pinning one provider (and a
#: sticky session) lets the agent loop's re-sent prefix be read from cache.
#:   AWOS_OPENROUTER_PROVIDER=deepinfra[,other]  -> provider.order
#:   AWOS_OPENROUTER_ALLOW_FALLBACKS=0|1         -> provider.allow_fallbacks (default 0)
#:   AWOS_SESSION_ID=<id>                        -> session_id (sticky routing)
#: All unset -> {} and requests are byte-for-byte what they were.
OPENROUTER_PROVIDER_ENV = "AWOS_OPENROUTER_PROVIDER"
OPENROUTER_FALLBACKS_ENV = "AWOS_OPENROUTER_ALLOW_FALLBACKS"
SESSION_ID_ENV = "AWOS_SESSION_ID"
_TRUE = ("1", "true", "yes", "on")


def requested_openrouter_providers() -> list[str]:
    """The provider order from AWOS_OPENROUTER_PROVIDER ([] when unset)."""
    raw = os.getenv(OPENROUTER_PROVIDER_ENV, "")
    return [part.strip() for part in raw.split(",") if part.strip()]


def openrouter_routing() -> dict:
    """OpenRouter routing fields for a request's extra_body; {} when unset."""
    body: dict = {}
    order = requested_openrouter_providers()
    if order:
        fallbacks = os.getenv(OPENROUTER_FALLBACKS_ENV, "0").strip().lower() in _TRUE
        body["provider"] = {"order": order, "allow_fallbacks": fallbacks}
    session = os.getenv(SESSION_ID_ENV, "").strip()
    if session:
        body["session_id"] = session
    return body


def with_openrouter_routing(kwargs: dict) -> dict:
    """`kwargs` with openrouter_routing() merged into its extra_body.

    Keys the caller already put in extra_body (e.g. `reasoning`) are kept and
    win on a clash. Env unset -> `kwargs` returned unchanged, no extra_body."""
    routing = openrouter_routing()
    if not routing:
        return kwargs
    body = dict(routing)
    body.update(kwargs.get("extra_body") or {})
    return {**kwargs, "extra_body": body}


def openrouter_key() -> Optional[str]:
    """The OpenRouter key, or None. None under AWOS_PROVIDER=local, so no
    caller routes (or renames a model) for OpenRouter while running local."""
    if local_mode():
        return None
    return os.getenv("OPENROUTER_API_KEY") or None


# ── Local provider (docs/specs/local_provider_spec.md) ───────────────────────

PROVIDER_ENV = "AWOS_PROVIDER"
LOCAL_BASE_URL_ENV = "AWOS_LOCAL_BASE_URL"
LOCAL_MODEL_ENV = "AWOS_LOCAL_MODEL"
LOCAL_API_KEY_ENV = "AWOS_LOCAL_API_KEY"
LOCAL_DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"
LOCAL_DEFAULT_MODEL = "qwen3.5-9b"
#: Accounting prefix: "local/<name>" prices at $0 (agent_loop.PRICES["local"]).
LOCAL_PREFIX = "local/"


def provider_pin() -> str:
    """AWOS_PROVIDER, normalised ("" when unset)."""
    return os.getenv(PROVIDER_ENV, "").strip().lower()


def local_mode() -> bool:
    """True when every model call should go to the local server."""
    if provider_pin() == "local":
        return True
    return os.getenv("AWOS_AGENT_MODEL", "").strip().lower().startswith(LOCAL_PREFIX)


def local_base_url() -> str:
    """AWOS_LOCAL_BASE_URL, else AWOS_BASE_URL, else http://127.0.0.1:8080/v1."""
    return (os.getenv(LOCAL_BASE_URL_ENV, "").strip()
            or os.getenv("AWOS_BASE_URL", "").strip()
            or LOCAL_DEFAULT_BASE_URL).rstrip("/")


def local_model_name() -> str:
    """The served model: AWOS_LOCAL_MODEL, else AWOS_AGENT_MODEL's local/<name>."""
    name = os.getenv(LOCAL_MODEL_ENV, "").strip()
    if name:
        return name[len(LOCAL_PREFIX):] if name.startswith(LOCAL_PREFIX) else name
    agent = os.getenv("AWOS_AGENT_MODEL", "").strip()
    if agent.lower().startswith(LOCAL_PREFIX) and len(agent) > len(LOCAL_PREFIX):
        return agent[len(LOCAL_PREFIX):]
    return LOCAL_DEFAULT_MODEL


def local_model_id(requested: Optional[str] = None) -> str:
    """The accounting id for a call made locally: "local/<name>".

    A requested "local/<x>" passes through; any other id (a cloud model a
    caller or the escalation ladder named) is served by the loaded model."""
    if requested and requested.strip().lower().startswith(LOCAL_PREFIX):
        return requested.strip()
    return LOCAL_PREFIX + local_model_name()


def local_wire_model(requested: Optional[str] = None) -> str:
    """The id sent to the server: local_model_id() without the prefix."""
    return local_model_id(requested)[len(LOCAL_PREFIX):]


def _local_key() -> str:
    # llama-server ignores the key unless started with --api-key; the SDK insists on one.
    return os.getenv(LOCAL_API_KEY_ENV, "").strip() or "not-needed"


def openrouter_model_id(model_id: str) -> str:
    """
    "claude-haiku-4-5" → "anthropic/claude-haiku-4.5", "gpt-4o-mini" →
    "openai/gpt-4o-mini". Ids that already carry a vendor pass through.
    """
    if "/" in model_id:
        return model_id
    for prefix, vendor in _VENDORS:
        if model_id.startswith(prefix):
            if vendor == "anthropic":
                # OpenRouter drops the date and writes versions with a dot.
                model_id = re.sub(r"-\d{8}$", "", model_id)
                model_id = re.sub(r"(\d)-(\d)", r"\1.\2", model_id)
            return f"{vendor}/{model_id}"
    return model_id


class _RewriteModel:
    """Every method call on `target` gets its `model=` mapped by `mapper`
    (default: to an OpenRouter id)."""

    def __init__(self, target: Any, chat: bool = False, mapper: Any = None) -> None:
        self._target = target
        # Chat-completions calls also get the provider pin / session id.
        self._chat = chat
        self._mapper = mapper or openrouter_model_id

    def __getattr__(self, name):
        attr = getattr(self._target, name)
        if not callable(attr):
            return attr

        def call(*args, **kwargs):
            if "model" in kwargs:
                kwargs["model"] = self._mapper(kwargs["model"])
            if self._chat:
                kwargs = with_openrouter_routing(kwargs)
            return attr(*args, **kwargs)

        return call


class _Rewritten:
    """A client whose `<path>.*(model=...)` calls — create, stream — get `model` mapped."""

    def __init__(self, inner: Any, path: tuple[str, ...], *, chat: bool, mapper: Any) -> None:
        self._inner = inner
        target = inner
        for part in path:
            target = getattr(target, part)
        namespace: Any = _RewriteModel(target, chat=chat, mapper=mapper)
        for part in reversed(path[1:]):
            namespace = SimpleNamespace(**{part: namespace})
        setattr(self, path[0], namespace)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _Routed(_Rewritten):
    """A client whose `<path>.*(model=...)` calls — create, stream — get OpenRouter ids."""

    def __init__(self, inner: Any, path: tuple[str, ...]) -> None:
        super().__init__(inner, path, chat=tuple(path) == ("chat", "completions"),
                         mapper=openrouter_model_id)


class _LocalRouted(_Rewritten):
    """A local-server client: every `model=` becomes the loaded model's id.

    Not a _Routed, so nothing OpenRouter-only (reasoning field, provider pin)
    is sent to the local server."""

    local = True

    def __init__(self, inner: Any, path: tuple[str, ...]) -> None:
        super().__init__(inner, path, chat=False, mapper=local_wire_model)


def is_local_client(client: Any) -> bool:
    return isinstance(client, _LocalRouted)


def local_chat_client(*, max_retries: Optional[int] = None) -> Any:
    """OpenAI-shaped client on the local server (AWOS_LOCAL_BASE_URL)."""
    options = client_options()
    if max_retries is not None:
        options["max_retries"] = max(0, int(max_retries))
    return _LocalRouted(
        OpenAI(api_key=_local_key(), base_url=local_base_url(), **options),
        ("chat", "completions"),
    )


def local_messages_client() -> Any:
    """Anthropic-shaped client on the local server (llama-server serves /v1/messages)."""
    base = local_base_url()
    if base.endswith("/v1"):
        base = base[: -len("/v1")]  # the Anthropic SDK appends /v1/messages itself
    return _LocalRouted(
        Anthropic(base_url=base, api_key=_local_key(), **client_options()),
        ("messages",),
    )


def chat_client(
    direct_key: Optional[str] = None,
    direct_base_url: Optional[str] = None,
    *,
    max_retries: Optional[int] = None,
) -> Any:
    """
    OpenAI-shaped client: OpenRouter when its key is set, else the direct
    provider. `max_retries` overrides the SDK retries (0 turns them off, for a
    caller that retries on its own terms).
    """
    if local_mode():
        return local_chat_client(max_retries=max_retries)
    options = client_options()
    if max_retries is not None:
        options["max_retries"] = max(0, int(max_retries))
    key = openrouter_key()
    if key:
        return _Routed(
            OpenAI(api_key=key, base_url=OPENROUTER_BASE_URL, **options),
            ("chat", "completions"),
        )
    if direct_key:
        return OpenAI(api_key=direct_key, base_url=direct_base_url, **options)
    return None


def messages_client(direct_key: Optional[str] = None) -> Any:
    """Anthropic-shaped client: OpenRouter's Messages endpoint when its key is set."""
    if local_mode():
        return local_messages_client()
    key = openrouter_key()
    if key:
        return _Routed(
            Anthropic(
                base_url=OPENROUTER_ANTHROPIC_BASE_URL, auth_token=key, api_key=None,
                **client_options(),
            ),
            ("messages",),
        )
    if direct_key:
        return Anthropic(api_key=direct_key, **client_options())
    return None


# ── Utility calls: bounded, checked, paid for ────────────────────────────────
#
# A utility call (notebook rewrite, integration review, critique) used to take
# whatever came back: a reasoning model that spent its output budget thinking
# returned "" or a reply cut off mid-sentence (finish_reason "length"), and a
# cut-off notebook rewrite overwrote a 2732-char notebook with 364 chars. Its
# spend went unrecorded too, so it never counted toward a goal's budget.
# utility_chat is opt-in: the agent loop and planner keep their own paths.

#: A utility call's output-token floor: room for low-effort reasoning plus
#: the answer. A cap, not a cost: unused tokens are not billed.
UTILITY_MIN_MAX_TOKENS = 4096


class ModelReplyError(RuntimeError):
    """A model reply that must not be used: empty or cut off."""

    def __init__(self, message: str, *, model: str = "", role: str = "",
                 finish_reason: Any = None, text: str = "", cost_usd: float = 0.0):
        super().__init__(message)
        self.model = model
        self.role = role
        self.finish_reason = finish_reason
        self.text = text
        self.cost_usd = cost_usd


class EmptyReplyError(ModelReplyError):
    """The model returned no content."""


class TruncatedReplyError(ModelReplyError):
    """The reply hit its token cap (finish_reason "length")."""


def _is_routed(client: Any) -> bool:
    return isinstance(client, _Routed)


def without_sdk_retries(client: Any) -> Any:
    """`client` with SDK retries off when it supports that; else `client` as is."""
    if _is_routed(client):
        inner = client.__dict__.get("_inner")
        if hasattr(inner, "with_options"):
            try:
                return _Routed(inner.with_options(max_retries=0), ("chat", "completions"))
            except Exception:
                return client
        return client
    if getattr(type(client), "with_options", None) is not None:
        try:
            return client.with_options(max_retries=0)
        except Exception:
            return client
    return client


def _record_utility_spend(request_type: str, model: str, response: Any, tracker: Any) -> float:
    """One call's tokens into TokenTracker + BudgetLedger; returns its USD cost."""
    try:
        from . import usage_record
    except ImportError:
        import usage_record
    try:
        from .agent_loop import _price_for
    except ImportError:
        from agent_loop import _price_for
    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    price_in, price_out = _price_for(model) or (0.0, 0.0)
    try:
        usage_record.record_api_usage(
            request_type=request_type,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_price=price_in,
            output_price=price_out,
            tracker=tracker,
        )
    except Exception:
        pass
    return usage_record.cost_from_tokens(input_tokens, output_tokens, price_in, price_out)


def utility_chat(
    client: Any,
    role: str,
    model: str,
    messages: list,
    *,
    max_tokens: Optional[int] = None,
    tracker: Any = None,
    request_type: Optional[str] = None,
    temperature: Optional[float] = 0,
    send_reasoning: Optional[bool] = None,
    sdk_retries: Optional[int] = None,
    reasoning: Any = "role",
    retry: bool = True,
    **extra: Any,
) -> tuple[str, dict]:
    """
    One checked utility call through an OpenAI-shaped client.

    - reasoning effort from reasoning_for(role), sent when the client routes
      through OpenRouter (or when `send_reasoning=True`);
    - max_tokens at least UTILITY_MIN_MAX_TOKENS;
    - an empty reply or finish_reason "length" is retried once with reasoning
      off, then raises EmptyReplyError / TruncatedReplyError;
    - every call's spend is recorded under `request_type` (default `role`),
      the wasted one included, so it counts toward the goal budget;
    - `sdk_retries=0` turns the SDK's own retries off;
    - `reasoning` overrides reasoning_for(role) (a dict, or None for none);
    - `retry=False` makes one call only: a bad reply raises at once (the
      caller handles it — one-shot falls back rather than pay twice).

    Returns (text, info), info = {"cost_usd", "calls", "finish_reason",
    "model"}. Transport errors propagate unchanged.
    """
    request_type = request_type or role
    if sdk_retries == 0:
        client = without_sdk_retries(client)
    if send_reasoning is None:
        send_reasoning = _is_routed(client)
    tokens = max(int(max_tokens or 0), UTILITY_MIN_MAX_TOKENS)
    # The retry switches reasoning off where it can; elsewhere it asks again.
    first = reasoning_for(role) if reasoning == "role" else reasoning
    attempts = [first, dict(REASONING_OFF)] if send_reasoning else [None, None]
    if not retry:
        attempts = attempts[:1]

    info: dict = {"cost_usd": 0.0, "calls": 0, "finish_reason": None, "model": model}
    text, finish = "", None
    for attempt, reasoning in enumerate(attempts, 1):
        kwargs: dict = dict(model=model, messages=messages, max_tokens=tokens, **extra)
        if temperature is not None:
            kwargs["temperature"] = temperature
        if reasoning is not None:
            body = dict(kwargs.pop("extra_body", None) or {})
            body["reasoning"] = reasoning
            kwargs["extra_body"] = body
        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:
            record_error(role, model, exc, attempt=attempt, final=True)
            raise
        info["calls"] += 1
        call_cost = _record_utility_spend(request_type, model, response, tracker)
        info["cost_usd"] += call_cost
        try:
            choice = response.choices[0]
            text = choice.message.content or ""
        except (AttributeError, IndexError, TypeError):
            choice, text = None, ""
        finish = getattr(choice, "finish_reason", None)
        info["finish_reason"] = finish
        good = bool(text.strip()) and finish != "length"
        record_response(role, model, response, cost_usd=call_cost, visible_chars=len(text),
                        attempt=attempt, final=good or attempt == len(attempts))
        if good:
            return text, info
    common = dict(model=model, role=role, finish_reason=finish, text=text,
                  cost_usd=info["cost_usd"])
    if finish == "length":
        raise TruncatedReplyError(
            f"{role} reply from {model} cut off at {tokens} tokens "
            f"(finish_reason='length') after {info['calls']} attempt(s)", **common)
    raise EmptyReplyError(
        f"{role} reply from {model} was empty (finish_reason={finish!r}) "
        f"after {info['calls']} attempt(s)", **common)
