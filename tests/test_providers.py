"""
Tests for providers.py — OpenRouter as the single provider key.

See docs/specs/openrouter_only_spec.md.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scaffold.agent import providers
from scaffold.agent.providers import chat_client, messages_client, openrouter_model_id

DIRECT_KEYS = (
    "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
    "OPENCODE_GO_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
)


@pytest.fixture
def openrouter(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")


@pytest.fixture
def no_openrouter(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


# ── id mapping ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bare, routed", [
    # Every id the codebase sends, checked against GET /api/v1/models 2026-09-23.
    ("deepseek-v4-flash", "deepseek/deepseek-v4-flash"),
    ("deepseek-v4-pro", "deepseek/deepseek-v4-pro"),
    ("deepseek-chat", "deepseek/deepseek-chat"),
    ("gpt-4o-mini", "openai/gpt-4o-mini"),
    ("claude-haiku-4-5", "anthropic/claude-haiku-4.5"),
    ("claude-haiku-4-5-20251001", "anthropic/claude-haiku-4.5"),
    ("claude-sonnet-4-6", "anthropic/claude-sonnet-4.6"),
    ("gemini-2.5-flash", "google/gemini-2.5-flash"),
    ("qwen3.7-plus", "qwen/qwen3.7-plus"),
    # Already namespaced: untouched.
    ("anthropic/claude-haiku-4.5", "anthropic/claude-haiku-4.5"),
])
def test_model_id_mapping(bare, routed):
    assert openrouter_model_id(bare) == routed


def test_unknown_vendor_passes_through():
    assert openrouter_model_id("mystery-model") == "mystery-model"


# ── precedence ───────────────────────────────────────────────────────────────

def test_openrouter_wins_over_direct_keys(openrouter, monkeypatch):
    for k in DIRECT_KEYS:
        monkeypatch.setenv(k, "direct-key")
    chat = chat_client("direct-key", "https://api.deepseek.com")
    msgs = messages_client("direct-key")
    assert "openrouter.ai" in str(chat.base_url)
    assert "openrouter.ai" in str(msgs.base_url)


def test_direct_provider_without_openrouter(no_openrouter):
    chat = chat_client("direct-key", "https://api.deepseek.com")
    assert "api.deepseek.com" in str(chat.base_url)
    assert "anthropic.com" in str(messages_client("direct-key").base_url)


def test_no_key_at_all_gives_none(no_openrouter):
    assert chat_client(None, "https://api.deepseek.com") is None
    assert messages_client(None) is None


# ── model rewriting on both client shapes ────────────────────────────────────

def test_chat_client_rewrites_model(openrouter):
    client = chat_client()
    inner = MagicMock()
    client.chat.completions._target = inner
    client.chat.completions.create(model="gpt-4o-mini", messages=[])
    assert inner.create.call_args.kwargs["model"] == "openai/gpt-4o-mini"


def test_messages_client_rewrites_create_and_stream(openrouter):
    client = messages_client()
    inner = MagicMock()
    client.messages._target = inner
    client.messages.create(model="claude-haiku-4-5", max_tokens=1, messages=[])
    client.messages.stream(model="claude-sonnet-4-6", max_tokens=1, messages=[])
    assert inner.create.call_args.kwargs["model"] == "anthropic/claude-haiku-4.5"
    assert inner.stream.call_args.kwargs["model"] == "anthropic/claude-sonnet-4.6"


# ── call sites ───────────────────────────────────────────────────────────────

def test_worker_routes_everything_through_openrouter(openrouter, monkeypatch):
    for k in DIRECT_KEYS:
        monkeypatch.delenv(k, raising=False)
    from scaffold.agent.worker import Worker

    w = Worker()
    for client in (w.opencode_client, w.client, w.anthropic_client, w.openai_client):
        assert client is not None
        assert "openrouter.ai" in str(client.base_url)


def test_worker_without_any_key_names_openrouter(no_openrouter, monkeypatch):
    for k in DIRECT_KEYS:
        monkeypatch.delenv(k, raising=False)
    from scaffold.agent.worker import Worker

    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        Worker()


def test_cheap_planner_needs_only_openrouter(openrouter, monkeypatch):
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    from scaffold.agent.cheap_planner import CheapPlanner

    assert "openrouter.ai" in str(CheapPlanner().client.base_url)
