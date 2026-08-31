"""Fixed provider contract for the initial AWOS coding-agent product.

The product path is deliberately single-provider (decision 2026-07-30). The
provider is selectable via ``AWOS_PROVIDER`` so the harness can be measured
when the default provider is unavailable — as happened on 2026-08-31, when
both OpenCode Go ladder models returned RegionError and the account balance
was empty, leaving the harness unable to make any call at all.

Selecting a profile changes *which* single provider is fixed. It does not
reintroduce dynamic multi-provider fallback.
"""

from __future__ import annotations

import os
from typing import Optional

from openai import OpenAI

_PROFILES: dict[str, dict] = {
    # Default. Unchanged from the original contract.
    "opencode": {
        "base_url": "https://opencode.ai/zen/go/v1",
        "key_env": "OPENCODE_GO_API_KEY",
        "cheap_model": "deepseek-v4-flash",
        "worker_model": "deepseek-v4-pro",
        "planner_model": "qwen3.7-plus",
        "cheap_price": (0.14, 0.28),   # $/MTok (input, output)
        "worker_price": (0.44, 0.88),
        "planner_max_tokens": 4096,
    },
    # Measurement profile. Anthropic exposes an OpenAI-compatible endpoint, so
    # every existing chat.completions call site works unchanged.
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1/",
        "key_env": "ANTHROPIC_API_KEY",
        "cheap_model": "claude-haiku-4-5-20251001",
        "worker_model": "claude-haiku-4-5-20251001",
        "planner_model": "claude-haiku-4-5-20251001",
        "cheap_price": (1.00, 5.00),
        "worker_price": (1.00, 5.00),
        # Without response_format forcing compact JSON, a full execution graph
        # overruns 4096 and returns truncated (unparseable) JSON.
        "planner_max_tokens": 16000,
    },
}

FIXED_PROVIDER = os.getenv("AWOS_PROVIDER", "opencode").strip().lower()
if FIXED_PROVIDER not in _PROFILES:
    raise ValueError(
        f"Unknown AWOS_PROVIDER={FIXED_PROVIDER!r}. "
        f"Known profiles: {', '.join(sorted(_PROFILES))}"
    )

PROFILE: dict = _PROFILES[FIXED_PROVIDER]

CHEAP_MODEL: str = PROFILE["cheap_model"]
FIXED_MODEL: str = PROFILE["worker_model"]
PLANNER_MODEL: str = PROFILE["planner_model"]
CHEAP_PRICE: tuple[float, float] = PROFILE["cheap_price"]
WORKER_PRICE: tuple[float, float] = PROFILE["worker_price"]
KEY_ENV: str = PROFILE["key_env"]
PLANNER_MAX_TOKENS: int = PROFILE["planner_max_tokens"]
DEFAULT_BASE_URL: str = PROFILE["base_url"]


def fixed_base_url() -> str:
    """Return the configured endpoint for the active profile."""
    return os.getenv("OPENCODE_GO_BASE_URL", DEFAULT_BASE_URL)


def fixed_api_key() -> Optional[str]:
    """Return the API key for the active profile, if set."""
    return os.getenv(KEY_ENV)


def provider_extra_kwargs() -> dict:
    """Provider-specific kwargs for chat.completions.create.

    OpenCode Go accepts response_format={"type": "json_object"} plus its own
    thinking toggle. Anthropic's OpenAI-compatibility layer rejects json_object
    (it requires json_schema) and does not take OpenCode's thinking parameter,
    so both are omitted and the prompt carries the JSON instruction instead.
    """
    if FIXED_PROVIDER == "anthropic":
        return {}
    return {
        "response_format": {"type": "json_object"},
        "extra_body": {"thinking": {"type": "disabled"}},
    }


def create_fixed_client(api_key: Optional[str] = None) -> OpenAI:
    """Create the only client allowed on the active product path."""
    key = api_key or fixed_api_key()
    if not key:
        raise ValueError(
            f"No API key for provider {FIXED_PROVIDER!r}. Set {KEY_ENV} in .env"
        )
    return OpenAI(api_key=key, base_url=fixed_base_url())
