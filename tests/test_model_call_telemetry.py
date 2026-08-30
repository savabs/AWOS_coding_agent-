import json

import pytest

from scaffold.agent.model_call_telemetry import (
    ModelCallTelemetryStore,
    record_model_call,
)


def test_record_model_call_persists_usage_context_and_tool_metrics(tmp_path):
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "TASK: fix it\nCODE: old code"},
        {"role": "assistant", "content": '{"action":"read_file","action_input":{}}'},
        {"role": "user", "content": "OBSERVATION (OK):\nline one"},
    ]
    store = ModelCallTelemetryStore(str(tmp_path))

    record = record_model_call(
        provider="opencode",
        model="test-model",
        request_type="react_worker",
        input_tokens=100,
        output_tokens=20,
        input_price=1.0,
        output_price=2.0,
        messages=messages,
        session_id="rs_test",
        task_id="task_1",
        step=2,
        store=store,
    )

    rows = store.read()
    assert len(rows) == 1
    assert rows[0]["total_tokens"] == 120
    assert rows[0]["estimated_cost_usd"] == pytest.approx(0.00014)
    assert rows[0]["tool_calls"] == 1
    assert rows[0]["tool_output_chars"] > 0
    assert rows[0]["context"]["system_instruction"] == 1
    assert rows[0]["session_id"] == "rs_test"
    assert record.model == "test-model"


def test_store_ignores_malformed_lines_and_aggregates(tmp_path):
    store = ModelCallTelemetryStore(str(tmp_path))
    store.path.write_text("not-json\n" + json.dumps({
        "input_tokens": 4,
        "output_tokens": 3,
        "total_tokens": 7,
        "estimated_cost_usd": 0.2,
        "tool_calls": 1,
        "tool_output_chars": 8,
        "latency_ms": 10,
        "context": {"current_task_prompt": 2},
    }) + "\n", encoding="utf-8")

    summary = store.aggregate()
    assert summary["calls"] == 1
    assert summary["total_tokens"] == 7
    assert summary["context"]["current_task_prompt"] == 2


def test_telemetry_write_failure_does_not_raise(tmp_path):
    store = ModelCallTelemetryStore(str(tmp_path / "missing"))
    store.path = tmp_path / "not-a-directory" / "model_calls.jsonl"
    record_model_call(
        provider="test",
        model="test",
        request_type="test",
        input_tokens=1,
        output_tokens=1,
        store=store,
    )


def test_aggregate_sums_cache_and_reasoning_tokens(tmp_path):
    store = ModelCallTelemetryStore(str(tmp_path))
    store.path.write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                {
                    "input_tokens": 100,
                    "output_tokens": 10,
                    "total_tokens": 110,
                    "estimated_cost_usd": 0.01,
                    "tool_calls": 0,
                    "tool_output_chars": 0,
                    "latency_ms": 5,
                    "cache_read_tokens": 60,
                    "cache_write_tokens": 40,
                    "reasoning_tokens": 3,
                    "context": {},
                },
                {
                    "input_tokens": 50,
                    "output_tokens": 5,
                    "total_tokens": 55,
                    "estimated_cost_usd": 0.005,
                    "tool_calls": 0,
                    "tool_output_chars": 0,
                    "latency_ms": 7,
                    "cache_read_tokens": 30,
                    "cache_write_tokens": 20,
                    "reasoning_tokens": 1,
                    "context": {},
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    summary = store.aggregate()
    assert summary["cache_read_tokens"] == 90
    assert summary["cache_write_tokens"] == 60
    assert summary["reasoning_tokens"] == 4
    assert summary["cache_hit_rate"] == 0.6  # 90 / 150 input tokens


def test_aggregate_cache_hit_rate_zero_when_no_input(tmp_path):
    store = ModelCallTelemetryStore(str(tmp_path))
    store.path.write_text(
        json.dumps(
            {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "estimated_cost_usd": 0.0,
                "tool_calls": 0,
                "tool_output_chars": 0,
                "latency_ms": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "reasoning_tokens": 0,
                "context": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert store.aggregate()["cache_hit_rate"] == 0.0
