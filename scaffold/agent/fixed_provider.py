"""Fixed provider contract for the initial AWOS coding-agent product."""

from __future__ import annotations

import os
from typing import Optional

from openai import OpenAI

FIXED_PROVIDER = "opencode"
FIXED_MODEL = "deepseek-v4-pro"
PLANNER_MODEL = "qwen3.7-plus"
DEFAULT_BASE_URL = "https://opencode.ai/zen/go/v1"


def fixed_base_url() -> str:
    """Return the configured OpenCode Go endpoint."""
    return os.getenv("OPENCODE_GO_BASE_URL", DEFAULT_BASE_URL)


def create_fixed_client(api_key: Optional[str] = None) -> OpenAI:
    """Create the only client allowed on the active product path."""
    key = api_key or os.getenv("OPENCODE_GO_API_KEY")
    if not key:
        raise ValueError("No OpenCode Go key found. Set OPENCODE_GO_API_KEY in .env")
    return OpenAI(api_key=key, base_url=fixed_base_url())
