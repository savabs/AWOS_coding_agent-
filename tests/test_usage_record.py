"""Tests for API token usage recording."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scaffold" / "agent"))

from usage_record import (
    cost_from_tokens,
    empty_usage,
    merge_result_usage,
    merge_usage,
    record_api_usage,
)


class _FakeTracker:
    def __init__(self):
        self.calls = []

    def record(self, request_type, model, input_tokens, output_tokens, cost):
        self.calls.append(
            {
                "request_type": request_type,
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost": cost,
            }
        )


def test_cost_from_tokens():
    # $1/MTok in, $2/MTok out → 1M in + 0.5M out = $2.00
    assert cost_from_tokens(1_000_000, 500_000, 1.0, 2.0) == pytest.approx(2.0)


def test_record_api_usage_returns_and_tracks():
    tracker = _FakeTracker()
    result = record_api_usage(
        request_type="worker",
        model="DeepSeek V4 Flash",
        input_tokens=1000,
        output_tokens=500,
        input_price=0.14,
        output_price=0.28,
        tracker=tracker,
    )
    assert result["input_tokens"] == 1000
    assert result["output_tokens"] == 500
    assert result["total_tokens"] == 1500
    assert result["cost_usd"] > 0
    assert len(tracker.calls) == 1
    assert tracker.calls[0]["model"] == "DeepSeek V4 Flash"


def test_merge_usage_accumulates():
    accum = empty_usage()
    merge_usage(accum, {"input_tokens": 100, "output_tokens": 50, "cost_usd": 0.01})
    merge_usage(accum, {"input_tokens": 200, "output_tokens": 100, "cost_usd": 0.02})
    assert accum["input_tokens"] == 300
    assert accum["output_tokens"] == 150
    assert accum["total_tokens"] == 450
    assert accum["cost_usd"] == pytest.approx(0.03)


def test_merge_result_usage_from_worker_dict():
    accum = empty_usage()
    merge_result_usage(accum, {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.001})
    merge_result_usage(accum, None)
    assert accum["total_tokens"] == 15
