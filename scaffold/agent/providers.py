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
#: Local default: a local server (AWOS_PROVIDER=local) on laptop hardware can
#: legitimately take minutes per long-context call; 120 s cut off live T4 runs
#: on the M5. Cloud keeps DEFAULT_MODEL_TIMEOUT_S.
DEFAULT_LOCAL_MODEL_TIMEOUT_S = 900.0
DEFAULT_MODEL_MAX_RETRIES = 2


def default_model_timeout_s() -> float:
    """900 s in local mode (AWOS_PROVIDER=local or a local/ agent model), else 120 s.

    Worst case: with the default 2 SDK retries a hung local call blocks for
    up to 3 x 900 s = 45 minutes (cloud: 3 x 120 s = 6 minutes). Lower
    AWOS_MODEL_TIMEOUT_S or AWOS_MODEL_MAX_RETRIES to bound it."""
    return DEFAULT_LOCAL_MODEL_TIMEOUT_S if local_mode() else DEFAULT_MODEL_TIMEOUT_S


def model_timeout_s() -> float:
    """AWOS_MODEL_TIMEOUT_S, else the mode default (120 cloud / 900 local);
    a bad or non-positive value falls back to the mode default."""
    default = default_model_timeout_s()
    raw = os.getenv("AWOS_MODEL_TIMEOUT_S", "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


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


# ── Cache-aware cost (docs/specs/prompt_caching.md) ──────────────────────────

#: Cached-input prices as a fraction of the model's input price (agent_loop
#: PRICES), per model family: (cache_read_factor, cache_write_factor).
#: deepseek-v4-flash: every pinned OpenRouter endpoint (deepinfra, gmicloud,
#: novita, siliconflow) bills a cache read at ~0.2x input (OpenRouter
#: /models/.../endpoints, 2026-10-09). A model priced in PRICES but missing
#: here gets no discount (1.0, 1.0): a cost cap should over- not under-count.
CACHE_PRICE_FACTORS: dict[str, tuple[float, float]] = {
    "deepseek-v4-flash": (0.2, 1.0),
    "deepseek-v4-pro": (0.2, 1.0),
    "deepseek-chat": (0.1, 1.0),
    "deepseek-reasoner": (0.1, 1.0),
    "qwen3.7-plus": (0.2, 1.0),
    "gemini-2.0-flash": (0.25, 1.0),
    "gpt-4o-mini": (0.5, 1.0),
    "claude-haiku-4-5": (0.1, 1.25),
    "claude-sonnet-4-6": (0.1, 1.25),
}
#: JSON file overriding prices per model id, in USD per million tokens:
#: {"deepseek-v4-flash": {"input": 0.09, "output": 0.18, "cache_read": 0.018,
#:  "cache_write": 0.09}} — any key may be omitted.
PRICE_TABLE_ENV = "AWOS_PRICE_TABLE"


def _price_overrides() -> dict:
    path = os.getenv(PRICE_TABLE_ENV, "").strip()
    if not path:
        return {}
    try:
        import json
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _model_keys(model: str) -> list[str]:
    keys = [model, model.rsplit("/", 1)[-1]]
    return keys + [k.replace(".", "-") for k in keys]


def price_table(model: str) -> dict:
    """USD per million tokens for `model`: input, output, cache_read, cache_write.

    Base prices come from agent_loop.PRICES (0 for an unpriced or local model);
    cache prices from CACHE_PRICE_FACTORS; AWOS_PRICE_TABLE overrides any of them.
    """
    try:
        from .agent_loop import _price_for
    except ImportError:
        from agent_loop import _price_for
    price_in, price_out = (_price_for(model or "") or (0.0, 0.0)) if model else (0.0, 0.0)
    read_f, write_f = 1.0, 1.0
    keys = _model_keys(model or "")
    match = next((k for k in keys if k in CACHE_PRICE_FACTORS), None) or next(
        (name for k in keys for name in CACHE_PRICE_FACTORS if k.startswith(name)), None)
    if match:
        read_f, write_f = CACHE_PRICE_FACTORS[match]
    table = {"input": price_in, "output": price_out,
             "cache_read": price_in * read_f, "cache_write": price_in * write_f}
    if not local_mode():
        overrides = _price_overrides()
        for key in keys:
            entry = overrides.get(key)
            if isinstance(entry, dict):
                for name in table:
                    value = entry.get(name)
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        table[name] = float(value)
                if "input" in entry and "cache_read" not in entry:
                    table["cache_read"] = table["input"] * read_f
                if "input" in entry and "cache_write" not in entry:
                    table["cache_write"] = table["input"] * write_f
                break
    return table


def _uget(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    value = getattr(obj, name, None)
    if value is None:
        extra = getattr(obj, "model_extra", None)
        if isinstance(extra, dict):
            value = extra.get(name)
    return value


def _uint(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def usage_tokens(usage: Any, timings: Any = None) -> dict:
    """Token counts from any usage shape we meet, normalised.

    Returns {"input", "cached", "cache_write", "output"} where `input` is the
    *whole* prompt (cached and written tokens included) and `cached` /
    `cache_write` are subsets of it. Shapes:
      - OpenAI / OpenRouter: prompt_tokens, prompt_tokens_details.cached_tokens
        and .cache_write_tokens (OpenRouter);
      - DeepSeek direct: prompt_cache_hit_tokens;
      - Anthropic: input_tokens excludes cache_read_input_tokens and
        cache_creation_input_tokens, so they are added back;
      - llama-server: `timings.cache_n` when usage carries no cached count.
    Missing fields count as 0; never raises.
    """
    out = {"input": 0, "cached": 0, "cache_write": 0, "output": 0}
    try:
        if _uget(usage, "prompt_tokens") is not None or _uget(usage, "completion_tokens") is not None:
            out["input"] = _uint(_uget(usage, "prompt_tokens"))
            out["output"] = _uint(_uget(usage, "completion_tokens"))
            details = _uget(usage, "prompt_tokens_details")
            out["cached"] = _uint(_uget(details, "cached_tokens")) or _uint(
                _uget(usage, "prompt_cache_hit_tokens"))
            out["cache_write"] = _uint(_uget(details, "cache_write_tokens"))
        elif usage is not None:  # Anthropic Messages shape
            read = _uint(_uget(usage, "cache_read_input_tokens"))
            write = _uint(_uget(usage, "cache_creation_input_tokens"))
            out["input"] = _uint(_uget(usage, "input_tokens")) + read + write
            out["output"] = _uint(_uget(usage, "output_tokens"))
            out["cached"], out["cache_write"] = read, write
        if not out["cached"] and timings is not None:
            out["cached"] = _uint(_uget(timings, "cache_n"))
        # A provider never caches more than it was sent.
        out["cached"] = min(out["cached"], out["input"])
        out["cache_write"] = min(out["cache_write"], out["input"] - out["cached"])
    except Exception:
        pass
    return out


def cache_hit_ratio(input_tokens: Any, cached_tokens: Any) -> Optional[float]:
    """cached / input, or None when either is unknown or input is 0."""
    try:
        if input_tokens is None or cached_tokens is None or int(input_tokens) <= 0:
            return None
        return round(min(1.0, max(0.0, int(cached_tokens) / int(input_tokens))), 4)
    except (TypeError, ValueError):
        return None


def estimate_cost_cached(model: str, input_tokens: int, output_tokens: int,
                         cached_tokens: int = 0, cache_write_tokens: int = 0) -> float:
    """USD cost with cached input priced at the cache-read rate.

    `input_tokens` is the whole prompt; `cached_tokens` and
    `cache_write_tokens` are the parts of it read from / written to cache.
    With no cache counts this equals agent_loop.estimate_cost.
    """
    table = price_table(model)
    total = _uint(input_tokens)
    cached = min(_uint(cached_tokens), total)
    written = min(_uint(cache_write_tokens), total - cached)
    fresh = total - cached - written
    return (fresh * table["input"] + cached * table["cache_read"]
            + written * table["cache_write"] + _uint(output_tokens) * table["output"]) / 1_000_000


def response_cost(model: str, response: Any) -> tuple[float, str]:
    """(USD, source) for one response: the provider's own `usage.cost` when it
    reports one (OpenRouter always does; it already prices cache reads), else
    estimate_cost_cached from the usage counts. source is "provider" or "estimate"."""
    usage = _uget(response, "usage")
    reported = _uget(usage, "cost")
    if isinstance(reported, (int, float)) and not isinstance(reported, bool):
        return float(reported), "provider"
    tok = usage_tokens(usage, _uget(response, "timings"))
    return estimate_cost_cached(model, tok["input"], tok["output"], tok["cached"],
                                tok["cache_write"]), "estimate"


def _record_utility_spend(request_type: str, model: str, response: Any, tracker: Any) -> float:
    """One call's tokens into TokenTracker + BudgetLedger; returns its USD cost.

    Cache-aware: cached input is priced at the cache-read rate. The ledger gets
    the call's blended input price so its USD matches the returned cost."""
    try:
        from . import usage_record
    except ImportError:
        import usage_record
    tok = usage_tokens(getattr(response, "usage", None), _uget(response, "timings"))
    input_tokens, output_tokens = tok["input"], tok["output"]
    table = price_table(model)
    cost = estimate_cost_cached(model, input_tokens, output_tokens, tok["cached"],
                                tok["cache_write"])
    price_out = table["output"]
    input_cost = cost - output_tokens * price_out / 1_000_000
    price_in = (input_cost * 1_000_000 / input_tokens) if input_tokens else table["input"]
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
    return cost


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
                        attempt=attempt, final=good or attempt == len(attempts),
                        request_type=request_type)
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
