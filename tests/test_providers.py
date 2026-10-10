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


def test_refine_goal_keeps_goal_when_reply_is_empty(openrouter):
    # A reasoning model can spend its whole budget thinking and return "".
    from scaffold.agent.cheap_planner import CheapPlanner

    planner = CheapPlanner()
    empty = MagicMock()
    empty.choices = [MagicMock(message=MagicMock(content=""))]
    planner.client = MagicMock()
    planner.client.chat.completions.create.return_value = empty
    assert planner.refine_goal("make it faster", {}) == "make it faster"


# ── request timeout ──────────────────────────────────────────────────────────
# The SDK default (600 s, 2 retries) let one hung agent-loop call stall a job
# for 12+ minutes. Every client AWOS builds carries AWOS_MODEL_TIMEOUT_S.

@pytest.fixture
def no_timeout_env(monkeypatch):
    monkeypatch.delenv("AWOS_MODEL_TIMEOUT_S", raising=False)
    monkeypatch.delenv("AWOS_MODEL_MAX_RETRIES", raising=False)


def _inner(client):
    return getattr(client, "_inner", client)


def test_client_options_defaults(no_timeout_env):
    assert providers.client_options() == {"timeout": 120.0, "max_retries": 2}


def test_client_options_from_env(monkeypatch):
    monkeypatch.setenv("AWOS_MODEL_TIMEOUT_S", "30")
    monkeypatch.setenv("AWOS_MODEL_MAX_RETRIES", "0")
    assert providers.client_options() == {"timeout": 30.0, "max_retries": 0}


@pytest.mark.parametrize("timeout, retries", [("abc", "x"), ("0", "-1"), ("-5", "")])
def test_client_options_bad_env_falls_back(monkeypatch, timeout, retries):
    monkeypatch.setenv("AWOS_MODEL_TIMEOUT_S", timeout)
    monkeypatch.setenv("AWOS_MODEL_MAX_RETRIES", retries)
    assert providers.client_options() == {"timeout": 120.0, "max_retries": 2}


@pytest.mark.parametrize("routed", [True, False])
def test_every_providers_client_gets_the_timeout(monkeypatch, routed):
    monkeypatch.setenv("AWOS_MODEL_TIMEOUT_S", "45")
    monkeypatch.setenv("AWOS_MODEL_MAX_RETRIES", "1")
    if routed:
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    else:
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    for client in (_inner(chat_client("k", "https://api.deepseek.com")),
                   _inner(messages_client("k"))):
        assert client.timeout == 45.0
        assert client.max_retries == 1


@pytest.mark.parametrize("env", [
    {"AWOS_BASE_URL": "http://localhost:11434/v1"},
    {"OPENROUTER_API_KEY": "sk-or-v1-test", "AWOS_AGENT_MODEL": "deepseek/deepseek-chat"},
    {"ANTHROPIC_API_KEY": "k"},
    {"DEEPSEEK_API_KEY": "k"},
    {"OPENAI_API_KEY": "k"},
])
def test_agent_loop_clients_get_the_timeout(monkeypatch, env):
    from scaffold.agent.agent_loop import build_client_from_env

    for name in ("AWOS_BASE_URL", "AWOS_PROVIDER", "OPENROUTER_API_KEY",
                 "AWOS_AGENT_MODEL", *DIRECT_KEYS):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("AWOS_MODEL_TIMEOUT_S", "33")
    monkeypatch.setenv("AWOS_MODEL_MAX_RETRIES", "1")
    sdk = build_client_from_env()._client
    assert sdk.timeout == 33.0
    assert sdk.max_retries == 1


def test_timed_out_model_call_is_a_clean_model_error_stop():
    import httpx
    from openai import APITimeoutError

    from scaffold.agent.agent_loop import AgentLoop, build_coding_registry

    class HangingClient:
        def complete(self, system, messages, registry, tool_choice=None):
            raise APITimeoutError(request=httpx.Request("POST", "https://x/v1"))

        def format_assistant_turn(self, reply):
            return {}

        def format_tool_results(self, calls, results):
            return []

    outcome = AgentLoop(build_coding_registry("."), HangingClient()).run("task")
    assert outcome.success is False
    assert outcome.stop_reason == "model_error"
    assert "APITimeoutError" in outcome.final_message
