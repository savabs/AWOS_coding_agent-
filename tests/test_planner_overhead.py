"""
Planner overhead: a reasoning model (DeepSeek V4 Flash) spent its whole
4096-token budget on hidden reasoning and returned "" / " " after 2–3 minutes
on 6 of 12 ordertool jobs per arm; the fallback then repeated the same call.

The CheapPlanner now caps reasoning effort (OpenRouter `reasoning` field),
has room for thinking (8192), and on an empty reply retries once with
reasoning off instead of failing into an identical fallback call.
"""
from unittest.mock import MagicMock

import pytest

from scaffold.agent import providers

PLAN_JSON = '{"plan": [{"task_id": 1, "file": "a.py", "action": "x", "complexity": "low"}]}'


def _reply(content, finish="stop", completion_tokens=100):
    reply = MagicMock()
    reply.choices = [MagicMock(message=MagicMock(content=content), finish_reason=finish)]
    reply.usage = MagicMock(prompt_tokens=900, completion_tokens=completion_tokens)
    return reply


@pytest.fixture
def openrouter(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key-for-test")
    monkeypatch.setenv("AWOS_PLANNER_MODEL", "deepseek/deepseek-v4-flash")
    monkeypatch.delenv("AWOS_PLANNER_REASONING", raising=False)


def _planner(*replies):
    from scaffold.agent.cheap_planner import CheapPlanner

    planner = CheapPlanner()
    planner.client = MagicMock()
    planner.client.chat.completions.create.side_effect = list(replies)
    return planner


def _calls(planner):
    return [c.kwargs for c in planner.client.chat.completions.create.call_args_list]


# ── providers.planner_reasoning ──────────────────────────────────────────────

@pytest.mark.parametrize("value, expected", [
    (None, {"effort": "low"}),
    ("", {"effort": "low"}),
    ("medium", {"effort": "medium"}),
    ("HIGH", {"effort": "high"}),
    ("off", {"enabled": False}),
    ("none", {"enabled": False}),
    ("default", None),
    ("bogus", {"effort": "low"}),
])
def test_planner_reasoning_env(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("AWOS_PLANNER_REASONING", raising=False)
    else:
        monkeypatch.setenv("AWOS_PLANNER_REASONING", value)
    assert providers.planner_reasoning() == expected


# ── request shape ────────────────────────────────────────────────────────────

def test_plan_request_caps_reasoning_and_has_room(openrouter):
    planner = _planner(_reply(PLAN_JSON))
    assert planner.plan("goal", {})["plan"][0]["file"] == "a.py"
    (call,) = _calls(planner)
    assert call["extra_body"] == {"reasoning": {"effort": "low"}}
    assert call["max_tokens"] >= 8192
    assert call["model"] == "deepseek/deepseek-v4-flash"


def test_reasoning_default_sends_no_field(openrouter, monkeypatch):
    monkeypatch.setenv("AWOS_PLANNER_REASONING", "default")
    planner = _planner(_reply(PLAN_JSON))
    planner.plan("goal", {})
    assert "extra_body" not in _calls(planner)[0]


def test_direct_provider_gets_no_reasoning_field(monkeypatch):
    """The `reasoning` field is OpenRouter's; OpenCode Go may reject it."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "fake-direct-key")
    planner = _planner(_reply(""), _reply(PLAN_JSON))
    with pytest.raises(RuntimeError, match="empty reply"):
        planner.plan("goal", {})
    # No OpenRouter → no reasoning switch → no retry either.
    calls = _calls(planner)
    assert len(calls) == 1 and "extra_body" not in calls[0]


# ── empty reply ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("content", ["", " ", None])
def test_empty_reply_retries_once_without_reasoning(openrouter, content):
    planner = _planner(_reply(content, finish="length", completion_tokens=4096), _reply(PLAN_JSON))
    tracker = MagicMock()
    plan = planner.plan("goal", {}, tracker=tracker)
    assert plan["plan"][0]["task_id"] == 1
    first, second = _calls(planner)
    assert first["extra_body"] == {"reasoning": {"effort": "low"}}
    assert second["extra_body"] == {"reasoning": {"enabled": False}}
    # Both calls were billed, the wasted one included.
    assert tracker.record.call_count == 2


def test_truncated_reply_retries(openrouter):
    planner = _planner(_reply('{"plan": [', finish="length"), _reply(PLAN_JSON))
    assert planner.plan("goal", {})["plan"]
    assert len(_calls(planner)) == 2


def test_still_empty_fails_clearly_not_as_json_error(openrouter):
    planner = _planner(_reply(" ", finish="length"), _reply("", finish="stop"))
    with pytest.raises(RuntimeError) as err:
        planner.plan("goal", {})
    assert "empty reply" in str(err.value)
    assert "invalid JSON" not in str(err.value)
    assert len(_calls(planner)) == 2


def test_reasoning_already_off_does_not_retry(openrouter, monkeypatch):
    monkeypatch.setenv("AWOS_PLANNER_REASONING", "off")
    planner = _planner(_reply(""), _reply(PLAN_JSON))
    with pytest.raises(RuntimeError, match="empty reply"):
        planner.plan("goal", {})
    assert len(_calls(planner)) == 1


def test_good_reply_is_one_call(openrouter):
    planner = _planner(_reply(PLAN_JSON))
    planner.plan("goal", {})
    assert len(_calls(planner)) == 1


def test_non_json_reply_still_reports_invalid_json(openrouter):
    planner = _planner(_reply("Here is the plan: step 1"))
    with pytest.raises(ValueError, match="invalid JSON"):
        planner.plan("goal", {})
    assert len(_calls(planner)) == 1
