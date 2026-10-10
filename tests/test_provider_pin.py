"""
OpenRouter provider pin (AWOS_OPENROUTER_PROVIDER / AWOS_SESSION_ID): every
OpenRouter chat call carries the routing fields in extra_body; nothing changes
when the env is unset; non-OpenRouter clients never get them. No network.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scaffold.agent import providers
from scaffold.agent.agent_loop import OpenAIToolClient, build_client_from_env
from scaffold.agent.cheap_planner import CheapPlanner
from scaffold.agent.providers import openrouter_routing, utility_chat

ENVS = ("AWOS_OPENROUTER_PROVIDER", "AWOS_OPENROUTER_ALLOW_FALLBACKS", "AWOS_SESSION_ID")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for env in ENVS:
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("AWOS_LLM_CALL_LOG", "0")


def _pin(monkeypatch, provider="deepinfra", session="sess-1", fallbacks=None):
    monkeypatch.setenv("AWOS_OPENROUTER_PROVIDER", provider)
    if session:
        monkeypatch.setenv("AWOS_SESSION_ID", session)
    if fallbacks is not None:
        monkeypatch.setenv("AWOS_OPENROUTER_ALLOW_FALLBACKS", fallbacks)


def _response(content="ok"):
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=2,
                            prompt_tokens_details=None, completion_tokens_details=None)
    message = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(model="m", usage=usage,
                           choices=[SimpleNamespace(message=message, finish_reason="stop")])


class _Recorder:
    """OpenAI-shaped fake: records every chat.completions.create kwargs."""

    def __init__(self):
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return _response()


class _Registry:
    def openai_schemas(self):
        return []


# ── openrouter_routing ───────────────────────────────────────────────────────

def test_routing_empty_when_unset():
    assert openrouter_routing() == {}
    assert providers.with_openrouter_routing({"a": 1}) == {"a": 1}


def test_routing_from_env(monkeypatch):
    _pin(monkeypatch, "deepinfra, novita ")
    assert openrouter_routing() == {
        "provider": {"order": ["deepinfra", "novita"], "allow_fallbacks": False},
        "session_id": "sess-1"}
    monkeypatch.setenv("AWOS_OPENROUTER_ALLOW_FALLBACKS", "1")
    assert openrouter_routing()["provider"]["allow_fallbacks"] is True


def test_session_id_alone(monkeypatch):
    monkeypatch.setenv("AWOS_SESSION_ID", "s")
    assert openrouter_routing() == {"session_id": "s"}


def test_merge_keeps_existing_extra_body(monkeypatch):
    _pin(monkeypatch)
    out = providers.with_openrouter_routing({"extra_body": {"reasoning": {"effort": "low"}}})
    assert out["extra_body"] == {"reasoning": {"effort": "low"}, "session_id": "sess-1",
                                 "provider": {"order": ["deepinfra"], "allow_fallbacks": False}}


# ── routed chat client (utility_chat) ────────────────────────────────────────

def _routed(rec):
    return providers._Routed(rec, ("chat", "completions"))


def test_utility_chat_unset_env_sends_reasoning_only():
    rec = _Recorder()
    utility_chat(_routed(rec), "notebook", "deepseek/deepseek-v4-flash",
                 [{"role": "user", "content": "x"}])
    assert set(rec.calls[0]["extra_body"]) == {"reasoning"}


def test_utility_chat_pinned_merges_with_reasoning(monkeypatch):
    _pin(monkeypatch)
    rec = _Recorder()
    utility_chat(_routed(rec), "notebook", "deepseek/deepseek-v4-flash",
                 [{"role": "user", "content": "x"}])
    body = rec.calls[0]["extra_body"]
    assert body["provider"] == {"order": ["deepinfra"], "allow_fallbacks": False}
    assert body["session_id"] == "sess-1" and "reasoning" in body


def test_unrouted_client_gets_nothing(monkeypatch):
    _pin(monkeypatch)
    rec = _Recorder()
    utility_chat(rec, "notebook", "m", [{"role": "user", "content": "x"}])
    assert "extra_body" not in rec.calls[0]


def test_messages_client_path_gets_nothing(monkeypatch):
    _pin(monkeypatch)
    seen = {}
    inner = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: seen.update(kw)))
    providers._Routed(inner, ("messages",)).messages.create(model="claude-haiku-4-5")
    assert "extra_body" not in seen


# ── planner ──────────────────────────────────────────────────────────────────

def _planner(via_openrouter=True):
    p = CheapPlanner.__new__(CheapPlanner)
    p.model_name = "deepseek/deepseek-v4-flash"
    p._via_openrouter = via_openrouter
    return p


def test_planner_request_unchanged_when_unset():
    assert _planner()._plan_request("p", {"effort": "low"})["extra_body"] == {
        "reasoning": {"effort": "low"}}
    assert "extra_body" not in _planner()._plan_request("p", None)


def test_planner_request_merges_pin_with_reasoning(monkeypatch):
    _pin(monkeypatch)
    body = _planner()._plan_request("p", {"effort": "low"})["extra_body"]
    assert body["reasoning"] == {"effort": "low"} and body["session_id"] == "sess-1"
    assert body["provider"]["order"] == ["deepinfra"]
    body = _planner()._plan_request("p", None)["extra_body"]
    assert "reasoning" not in body and body["provider"]["order"] == ["deepinfra"]


def test_planner_direct_provider_gets_nothing(monkeypatch):
    _pin(monkeypatch)
    assert "extra_body" not in _planner(via_openrouter=False)._plan_request("p", None)


# ── agent loop ───────────────────────────────────────────────────────────────

def test_agent_loop_openrouter_request_carries_pin(monkeypatch):
    _pin(monkeypatch)
    rec = _Recorder()
    OpenAIToolClient(rec, "m", openrouter=True).complete("sys", [], _Registry(),
                                                         tool_choice="finish")
    call = rec.calls[0]
    assert call["extra_body"] == {"provider": {"order": ["deepinfra"], "allow_fallbacks": False},
                                  "session_id": "sess-1"}
    assert call["tool_choice"]["function"]["name"] == "finish"


def test_agent_loop_request_unchanged_when_unset():
    rec = _Recorder()
    OpenAIToolClient(rec, "m", openrouter=True).complete("sys", [], _Registry())
    assert set(rec.calls[0]) == {"model", "max_tokens", "messages", "tools"}


def test_agent_loop_non_openrouter_gets_nothing(monkeypatch):
    _pin(monkeypatch)
    rec = _Recorder()
    OpenAIToolClient(rec, "m").complete("sys", [], _Registry())
    assert "extra_body" not in rec.calls[0]


def test_build_client_from_env_marks_openrouter(monkeypatch):
    for env in ("AWOS_BASE_URL", "AWOS_PROVIDER"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-real")
    client = build_client_from_env("deepseek/deepseek-v4-flash")
    assert isinstance(client, OpenAIToolClient) and client.openrouter is True
    monkeypatch.setenv("AWOS_PROVIDER", "local")
    monkeypatch.setenv("AWOS_BASE_URL", "http://localhost:11434/v1")
    assert build_client_from_env("q").openrouter is False
