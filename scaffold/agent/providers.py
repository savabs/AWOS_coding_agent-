"""
providers.py — one place that decides where a model call goes.

With OPENROUTER_API_KEY set, every client routes through OpenRouter and direct
provider keys are ignored: one key, one balance, one way to fail. Without it,
callers fall back to the direct provider they name.

See docs/specs/openrouter_only_spec.md.
"""
from __future__ import annotations

import os
import re
from types import SimpleNamespace
from typing import Any, Optional

from anthropic import Anthropic
from openai import OpenAI

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


def openrouter_key() -> Optional[str]:
    return os.getenv("OPENROUTER_API_KEY") or None


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
    """Every method call on `target` gets its `model=` mapped to an OpenRouter id."""

    def __init__(self, target: Any) -> None:
        self._target = target

    def __getattr__(self, name):
        attr = getattr(self._target, name)
        if not callable(attr):
            return attr

        def call(*args, **kwargs):
            if "model" in kwargs:
                kwargs["model"] = openrouter_model_id(kwargs["model"])
            return attr(*args, **kwargs)

        return call


class _Routed:
    """A client whose `<path>.*(model=...)` calls — create, stream — get OpenRouter ids."""

    def __init__(self, inner: Any, path: tuple[str, ...]) -> None:
        self._inner = inner
        target = inner
        for part in path:
            target = getattr(target, part)
        namespace: Any = _RewriteModel(target)
        for part in reversed(path[1:]):
            namespace = SimpleNamespace(**{part: namespace})
        setattr(self, path[0], namespace)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def chat_client(
    direct_key: Optional[str] = None, direct_base_url: Optional[str] = None
) -> Any:
    """OpenAI-shaped client: OpenRouter when its key is set, else the direct provider."""
    key = openrouter_key()
    if key:
        return _Routed(
            OpenAI(api_key=key, base_url=OPENROUTER_BASE_URL), ("chat", "completions")
        )
    if direct_key:
        return OpenAI(api_key=direct_key, base_url=direct_base_url)
    return None


def messages_client(direct_key: Optional[str] = None) -> Any:
    """Anthropic-shaped client: OpenRouter's Messages endpoint when its key is set."""
    key = openrouter_key()
    if key:
        return _Routed(
            Anthropic(base_url=OPENROUTER_ANTHROPIC_BASE_URL, auth_token=key, api_key=None),
            ("messages",),
        )
    if direct_key:
        return Anthropic(api_key=direct_key)
    return None
