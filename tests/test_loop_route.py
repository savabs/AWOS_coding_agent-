"""Tests for loop_route.py: the cost-routing ladder (rea_synthesis A6, A8)."""

import json

import pytest

from scaffold.agent import loop_route as lr


def test_ladder_parsing(monkeypatch):
    monkeypatch.delenv("AWOS_LOOP_ROUTE", raising=False)
    assert lr.route_ladder() == []
    monkeypatch.setenv("AWOS_LOOP_ROUTE", " local , claude-haiku-5-5,, ")
    assert lr.route_ladder() == ["local", "claude-haiku-5-5"]


@pytest.mark.parametrize("raw,want", [("", 30), ("12", 12), ("0", 1), ("x", 30)])
def test_rung_steps(monkeypatch, raw, want):
    monkeypatch.setenv("AWOS_LOOP_ROUTE_RUNG_STEPS", raw)
    assert lr.rung_steps() == want


def test_is_local_spec():
    assert lr.is_local_spec("local") and lr.is_local_spec("Local/qwen3.5-9b")
    assert not lr.is_local_spec("claude-haiku-5-5") and not lr.is_local_spec("localish")


def test_local_rung_is_grammar_wrapped_and_free(monkeypatch):
    monkeypatch.delenv("AWOS_PROVIDER", raising=False)
    monkeypatch.delenv("AWOS_LOOP_GRAMMAR", raising=False)
    from scaffold.agent.agent_loop import estimate_cost
    from scaffold.agent.loop_grammar import GrammarLoopClient

    client = lr.build_route_client("local/qwen-test")
    assert isinstance(client, GrammarLoopClient)
    assert client.model == "local/qwen-test"
    assert estimate_cost(client.model, 10_000, 1_000) == 0.0


def test_local_rung_without_grammar(monkeypatch):
    monkeypatch.setenv("AWOS_LOOP_GRAMMAR", "0")
    from scaffold.agent.agent_loop import OpenAIToolClient

    assert type(lr.build_route_client("local")) is OpenAIToolClient


def test_claude_rung_needs_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        lr.build_route_client("claude-haiku-5-5")


def test_claude_rung_builds_anthropic_client(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-key")
    from scaffold.agent.agent_loop import AnthropicToolClient

    client = lr.build_route_client("claude-haiku-5-5")
    assert isinstance(client, AnthropicToolClient) and client.model == "claude-haiku-5-5"


def test_handover_note_lists_commands_and_files():
    transcript = [{"step": i, "commands": [f"cmd{i}"], "ok": i % 2 == 0} for i in range(1, 20)]
    note = lr.handover_note(transcript, ["a.py", "b.py"], "stuck: failure_rate", last_n=3)
    assert "stuck: failure_rate" in note and "a.py, b.py" in note
    assert "step 17: cmd17 [failed]" in note and "step 19: cmd19 [failed]" in note
    assert "cmd16" not in note
    assert "(none)" in lr.handover_note([], [], "x")


def test_record_decisions_labels_each_line(tmp_path, monkeypatch):
    path = tmp_path / "route.jsonl"
    monkeypatch.setenv("AWOS_ROUTE_LOG", str(path))
    lr.record_decisions([{"from": "local", "to": "claude-haiku-5-5", "reason": "stuck", "step": 9},
                         {"start": "local", "step": 0}],
                        goal_key="k", solved=True, stop_reason="submitted", cost_usd=0.01, steps=20)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 2
    assert all(r["label_solved"] is True and r["task"] == "k" and r["label_steps"] == 20 for r in rows)


def test_record_decisions_off_under_pytest_by_default(monkeypatch):
    monkeypatch.delenv("AWOS_ROUTE_LOG", raising=False)
    assert lr.log_path() is None
    monkeypatch.setenv("AWOS_ROUTE_LOG", "0")
    assert lr.log_path() is None
    lr.record_decisions([{"x": 1}], goal_key="k", solved=False, stop_reason="max_steps",
                        cost_usd=0, steps=1)  # no-op, no raise


def test_record_decisions_never_raises(tmp_path, monkeypatch):
    target = tmp_path / "dir_not_file"
    target.mkdir()
    monkeypatch.setenv("AWOS_ROUTE_LOG", str(target))
    lr.record_decisions([{"x": 1}], goal_key="k", solved=False, stop_reason="max_steps",
                        cost_usd=0, steps=1)
