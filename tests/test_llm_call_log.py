"""
llm_call_log: one JSON line per model call, never raising; hooked into
utility_chat, the agent loop and the planner. Hermetic: fake clients only.
"""
from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scaffold.agent import llm_call_log as lcl
from scaffold.agent.agent_loop import AgentLoop, OpenAIToolClient
from scaffold.agent.cheap_planner import CheapPlanner
from scaffold.agent.providers import EmptyReplyError, TruncatedReplyError, utility_chat

RECORD = "scaffold.agent.usage_record.record_api_usage"


@pytest.fixture
def log(tmp_path, monkeypatch):
    path = tmp_path / "calls.jsonl"
    monkeypatch.setenv("AWOS_LLM_CALL_LOG", str(path))
    for env in ("AWOS_EVAL_ARM", "AWOS_EVAL_JOB", "AWOS_EVAL_REPEAT"):
        monkeypatch.delenv(env, raising=False)

    def lines():
        if not path.exists():
            return []
        return [json.loads(x) for x in path.read_text().splitlines()]
    return lines


def _response(content="ok", finish="stop", model="deepseek/deepseek-v4-flash",
              prompt=100, completion=20, reasoning=None, cached=None, cost=None, tool_calls=None):
    usage = SimpleNamespace(
        prompt_tokens=prompt, completion_tokens=completion,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=reasoning))
    if cost is not None:
        usage.cost = cost
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(model=model, usage=usage,
                           choices=[SimpleNamespace(message=message, finish_reason=finish)])


class _Client:
    def __init__(self, *replies, error=None):
        self._replies = list(replies)
        self._error = error
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        if self._error:
            raise self._error
        return self._replies.pop(0)


# ── record_call ──────────────────────────────────────────────────────────────

def test_record_call_writes_one_line_with_all_fields(log):
    lcl.record_call("notebook", "a/m", "a/m-2026", "stop", 10, 5, reasoning_tokens=3,
                    cached_tokens=2, cost_usd=0.01, visible_chars=42)
    [line] = log()
    assert line["component"] == "notebook" and line["response_model"] == "a/m-2026"
    assert line["reasoning_tokens"] == 3 and line["cached_tokens"] == 2
    assert line["visible_chars"] == 42 and line["error"] is None
    assert isinstance(line["ts"], float) and line["pid"] == os.getpid()


def test_record_call_copies_eval_tags(log, monkeypatch):
    monkeypatch.setenv("AWOS_EVAL_JOB", "7")
    lcl.record_call("agent", "m", "m", "stop", 1, 1)
    assert log()[0]["job"] == "7"


@pytest.mark.parametrize("value", ["0", "off", "false", ""])
def test_disabled_flag_writes_nothing(tmp_path, monkeypatch, value):
    monkeypatch.setenv("AWOS_LLM_CALL_LOG", value)
    monkeypatch.chdir(tmp_path)
    lcl.record_call("agent", "m", "m", "stop", 1, 1)
    assert not (tmp_path / ".awos").exists()


def test_default_path_is_cwd_awos_outside_pytest(tmp_path, monkeypatch):
    monkeypatch.delenv("AWOS_LLM_CALL_LOG", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.chdir(tmp_path)
    lcl.record_call("agent", "m", "m", "stop", 1, 1)
    assert (tmp_path / ".awos" / "llm_calls.jsonl").read_text().count("\n") == 1


def test_off_under_pytest_without_explicit_path(monkeypatch):
    monkeypatch.delenv("AWOS_LLM_CALL_LOG", raising=False)
    assert lcl.log_path() is None


def test_record_call_never_raises(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("AWOS_LLM_CALL_LOG", str(blocker / "sub" / "calls.jsonl"))
    lcl.record_call("agent", "m", "m", "stop", 1, 1)  # mkdir under a file fails silently
    lcl.record_call("agent", object(), object(), None, None, None, weird=object())
    lcl.record_response("agent", "m", object())
    lcl.record_error("agent", "m", RuntimeError("x"))


def test_fields_from_openai_response():
    f = lcl.fields_from_response(_response("hello", "length", reasoning=900, cached=64, cost=0.002))
    assert f == {"response_model": "deepseek/deepseek-v4-flash", "finish_reason": "length",
                 "input_tokens": 100, "output_tokens": 20, "reasoning_tokens": 900,
                 "cached_tokens": 64, "cost_usd": 0.002, "visible_chars": 5}


def test_fields_from_dict_and_anthropic_and_garbage():
    d = {"model": "x", "usage": {"prompt_tokens": 3, "completion_tokens": 4,
                                 "completion_tokens_details": {"reasoning_tokens": 2}},
         "choices": [{"finish_reason": "stop", "message": {"content": None}}]}
    f = lcl.fields_from_response(d)
    assert f["reasoning_tokens"] == 2 and f["visible_chars"] == 0 and f["cached_tokens"] is None
    a = SimpleNamespace(model="claude", stop_reason="max_tokens",
                        content=[SimpleNamespace(type="text", text="abc")],
                        usage=SimpleNamespace(input_tokens=1, output_tokens=2,
                                              cache_read_input_tokens=9))
    f = lcl.fields_from_response(a)
    assert (f["finish_reason"], f["visible_chars"], f["cached_tokens"]) == ("max_tokens", 3, 9)
    assert lcl.fields_from_response(None)["response_model"] is None


# ── hooks ────────────────────────────────────────────────────────────────────

def test_utility_chat_logs_every_attempt(log):
    client = _Client(_response("", "length", reasoning=4000), _response("fine"))
    with patch(RECORD):
        text, info = utility_chat(client, "notebook", "deepseek/deepseek-v4-flash",
                                  [{"role": "user", "content": "x"}], send_reasoning=True)
    assert text == "fine"
    first, second = log()
    assert first["component"] == "notebook" and first["finish_reason"] == "length"
    assert first["visible_chars"] == 0 and first["reasoning_tokens"] == 4000
    assert first["final"] is False and second["final"] is True and second["attempt"] == 2


@pytest.mark.parametrize("finish,error", [("length", TruncatedReplyError), ("stop", EmptyReplyError)])
def test_utility_chat_logs_failed_replies(log, finish, error):
    client = _Client(_response("", finish), _response("", finish))
    with patch(RECORD), pytest.raises(error):
        utility_chat(client, "critique", "m", [], send_reasoning=True)
    lines = log()
    assert [x["component"] for x in lines] == ["critique", "critique"]
    assert lines[-1]["final"] is True and lines[-1]["visible_chars"] == 0


def test_utility_chat_logs_transport_error(log):
    with pytest.raises(ConnectionError):
        utility_chat(_Client(error=ConnectionError("down")), "review", "m", [])
    [line] = log()
    assert line["component"] == "review" and "ConnectionError" in line["error"]


class _Registry:
    project_root = None

    def openai_schemas(self):
        return []


def test_agent_loop_logs_each_reply(log):
    replies = [_response("", "length", completion=16384), _response("", "length"),
               _response("done", "stop")]
    client = OpenAIToolClient(_Client(*replies), "deepseek/deepseek-v4-flash")
    loop = AgentLoop(registry=_Registry(), client=client, max_turns=5)
    loop.run("task")
    lines = log()
    assert len(lines) == 3 and {x["component"] for x in lines} == {"agent"}
    assert [x["finish_reason"] for x in lines] == ["length", "length", "stop"]
    assert lines[0]["requested_model"] == "deepseek/deepseek-v4-flash"
    assert lines[0]["visible_chars"] == 0 and lines[0]["tool_calls"] == 0
    assert lines[2]["visible_chars"] == 4 and lines[0]["turn"] == 1


def test_agent_loop_logs_model_error(log):
    client = OpenAIToolClient(_Client(error=TimeoutError("slow")), "m")
    outcome = AgentLoop(registry=_Registry(), client=client, max_turns=3).run("task")
    assert outcome.stop_reason == "model_error"
    [line] = log()
    assert line["component"] == "agent" and "TimeoutError" in line["error"]


def _planner(*replies, error=None):
    p = CheapPlanner.__new__(CheapPlanner)
    p.model_name = "deepseek/deepseek-v4-flash"
    p._via_openrouter = True
    p.client = _Client(*replies, error=error)
    return p


def test_planner_logs_each_attempt_including_empty(log, monkeypatch):
    monkeypatch.delenv("AWOS_PLANNER_REASONING", raising=False)
    p = _planner(_response(" ", "length"), _response('{"plan": []}'))
    p._complete_plan("goal")
    lines = log()
    assert [x["component"] for x in lines] == ["planner"] * len(lines)
    assert lines[-1]["visible_chars"] == len('{"plan": []}') and lines[-1]["final"] is True
    if len(lines) == 2:
        assert lines[0]["finish_reason"] == "length" and lines[0]["final"] is False


def test_planner_logs_transport_error(log):
    with pytest.raises(RuntimeError):
        _planner(error=RuntimeError("boom"))._complete_plan("goal")
    [line] = log()
    assert line["component"] == "planner" and "boom" in line["error"]
