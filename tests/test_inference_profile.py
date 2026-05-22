"""
Tests for scaffold/agent/core/inference_profile.py — Phase 3: Inference Visibility.

Test classes:
  TestTokenProfile      — per-task prefill/decode decomposition
  TestInferenceReport   — aggregate metrics, workload classification, caching
  TestBatchThroughput   — batching model properties
  TestInferenceProfiler — from_spans factory, edge cases
  TestEdgeCases         — missing tokens, zero latency, single span
"""

import sys
import os
import math
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scaffold.agent.core.inference_profile import (
    TokenProfile,
    InferenceReport,
    InferenceProfiler,
    CACHE_SAVINGS_RATIO,
    BATCH_LATENCY_OVERHEAD,
    PREFILL_HEAVY_THRESHOLD,
    DECODE_HEAVY_THRESHOLD,
    MIN_TOKENS,
)
from scaffold.agent.core.observability import TaskSpan


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_span(
    input_tokens: int = 1000,
    output_tokens: int = 200,
    worker_latency_ms: float = 1200.0,
    cost_usd: float = 0.002,
    model: str = "deepseek",
    success: bool = True,
) -> TaskSpan:
    base = time.time()
    return TaskSpan(
        span_id="abc",
        task_id="t1",
        goal="g",
        action="a",
        file="f.py",
        enqueue_ts=base,
        routing_ts=base + 0.01,
        worker_start_ts=base + 0.02,
        complete_ts=base + 0.02 + worker_latency_ms / 1000.0,
        model_chosen=model,
        success=success,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
    )


def _make_profile(
    input_tokens: int = 800,
    output_tokens: int = 200,
    worker_latency_ms: float = 1000.0,
    cost_usd: float = 0.002,
    model: str = "deepseek",
) -> TokenProfile:
    span = _make_span(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        worker_latency_ms=worker_latency_ms,
        cost_usd=cost_usd,
        model=model,
    )
    return InferenceProfiler.per_task(span)


# ── TestTokenProfile ──────────────────────────────────────────────────────────

class TestTokenProfile:
    def test_prefill_ratio(self):
        p = _make_profile(input_tokens=800, output_tokens=200)
        assert abs(p.prefill_ratio - 0.8) < 1e-9

    def test_decode_ratio(self):
        p = _make_profile(input_tokens=800, output_tokens=200)
        assert abs(p.decode_ratio - 0.2) < 1e-9

    def test_prefill_plus_decode_equals_one(self):
        p = _make_profile(input_tokens=600, output_tokens=400)
        assert abs(p.prefill_ratio + p.decode_ratio - 1.0) < 1e-9

    def test_token_efficiency(self):
        p = _make_profile(input_tokens=500, output_tokens=100)
        assert abs(p.token_efficiency - 0.2) < 1e-9

    def test_total_tokens(self):
        p = _make_profile(input_tokens=700, output_tokens=300)
        assert p.total_tokens == 1000

    def test_ttft_proportional_to_prefill(self):
        p = _make_profile(input_tokens=800, output_tokens=200, worker_latency_ms=1000.0)
        assert abs(p.estimated_ttft_ms - 800.0) < 1.0

    def test_tpot_is_decode_ms_over_output_tokens(self):
        p = _make_profile(input_tokens=800, output_tokens=200, worker_latency_ms=1000.0)
        # decode_ms = 1000 × 0.2 = 200ms; tpot = 200/200 = 1.0ms/tok
        assert abs(p.estimated_tpot_ms - 1.0) < 0.01

    def test_cache_savings_proportional_to_cost(self):
        p = _make_profile(input_tokens=800, output_tokens=200, cost_usd=0.010)
        expected = 0.010 * 0.8 * CACHE_SAVINGS_RATIO
        assert abs(p.cache_savings_usd - expected) < 1e-9

    def test_decode_throughput_positive(self):
        p = _make_profile(output_tokens=200, worker_latency_ms=1000.0)
        assert p.decode_throughput_tps > 0

    def test_prefill_throughput_positive(self):
        p = _make_profile(input_tokens=800, worker_latency_ms=1000.0)
        assert p.prefill_throughput_tps > 0

    def test_is_prefill_heavy_true(self):
        p = _make_profile(input_tokens=900, output_tokens=100)
        assert p.prefill_ratio >= PREFILL_HEAVY_THRESHOLD
        assert p.is_prefill_heavy is True

    def test_is_decode_heavy_true(self):
        p = _make_profile(input_tokens=100, output_tokens=900)
        assert p.prefill_ratio <= DECODE_HEAVY_THRESHOLD
        assert p.is_decode_heavy is True

    def test_workload_label_prefill_heavy(self):
        p = _make_profile(input_tokens=900, output_tokens=100)
        assert p.workload_label == "prefill_heavy"

    def test_workload_label_decode_heavy(self):
        p = _make_profile(input_tokens=100, output_tokens=900)
        assert p.workload_label == "decode_heavy"

    def test_workload_label_balanced(self):
        p = _make_profile(input_tokens=500, output_tokens=500)
        assert p.workload_label == "balanced"

    def test_model_field_set(self):
        p = _make_profile(model="sonnet")
        assert p.model == "sonnet"


# ── TestInferenceReport ───────────────────────────────────────────────────────

class TestInferenceReport:
    def _report(self, **kwargs) -> InferenceReport:
        spans = [_make_span(**kwargs) for _ in range(5)]
        return InferenceProfiler.from_spans(spans)

    def test_n_samples(self):
        spans = [_make_span() for _ in range(7)]
        r = InferenceProfiler.from_spans(spans)
        assert r.n_samples == 7

    def test_avg_prefill_ratio_correct(self):
        # 800 input / 1000 total = 0.8
        r = self._report(input_tokens=800, output_tokens=200)
        assert abs(r.avg_prefill_ratio - 0.8) < 0.01

    def test_workload_type_prefill_heavy(self):
        r = self._report(input_tokens=900, output_tokens=100)
        assert r.workload_type == "prefill_heavy"

    def test_workload_type_decode_heavy(self):
        r = self._report(input_tokens=100, output_tokens=900)
        assert r.workload_type == "decode_heavy"

    def test_workload_type_balanced(self):
        r = self._report(input_tokens=500, output_tokens=500)
        assert r.workload_type == "balanced"

    def test_total_cache_savings_positive(self):
        r = self._report(input_tokens=800, output_tokens=200, cost_usd=0.005)
        assert r.total_cache_savings_usd > 0

    def test_cache_savings_pct_bounded(self):
        r = self._report(input_tokens=800, output_tokens=200, cost_usd=0.005)
        assert 0.0 <= r.cache_savings_pct <= 100.0

    def test_cache_savings_pct_near_expected(self):
        # prefill=0.8, cache_ratio=0.9 → cache_savings ≈ 72% of cost
        r = self._report(input_tokens=800, output_tokens=200, cost_usd=0.010)
        expected_pct = 0.8 * CACHE_SAVINGS_RATIO * 100
        assert abs(r.cache_savings_pct - expected_pct) < 2.0

    def test_batch_gains_monotonically_increasing(self):
        r = self._report()
        assert r.batch_gain_x2 > 1.0
        assert r.batch_gain_x4 > r.batch_gain_x2
        assert r.batch_gain_x8 > r.batch_gain_x4

    def test_model_prefill_ratios_populated(self):
        spans = [
            _make_span(model="deepseek", input_tokens=800, output_tokens=200),
            _make_span(model="sonnet", input_tokens=600, output_tokens=400),
        ]
        r = InferenceProfiler.from_spans(spans)
        assert "deepseek" in r.model_prefill_ratios
        assert "sonnet" in r.model_prefill_ratios

    def test_model_prefill_ratio_accuracy(self):
        spans = [_make_span(model="haiku", input_tokens=700, output_tokens=300)] * 4
        r = InferenceProfiler.from_spans(spans)
        assert abs(r.model_prefill_ratios["haiku"] - 0.7) < 0.01

    def test_display_returns_string(self):
        r = self._report()
        text = r.display()
        assert isinstance(text, str)
        assert "INFERENCE PROFILE" in text

    def test_display_contains_key_sections(self):
        r = self._report(input_tokens=800, output_tokens=200)
        text = r.display()
        assert "Prefill" in text
        assert "Decode" in text
        assert "Cache savings" in text
        assert "Batching gain" in text

    def test_avg_tpot_positive(self):
        r = self._report(input_tokens=800, output_tokens=200, worker_latency_ms=1000.0)
        assert r.avg_tpot_ms > 0

    def test_avg_ttft_less_than_total_latency(self):
        r = self._report(input_tokens=800, output_tokens=200, worker_latency_ms=1000.0)
        # TTFT should be ≤ total latency
        assert r.avg_ttft_ms <= 1000.0 + 1e-6


# ── TestBatchThroughput ───────────────────────────────────────────────────────

class TestBatchThroughput:
    def test_batch_1_returns_1(self):
        assert InferenceProfiler.batch_throughput_factor(1) == 1.0

    def test_batch_0_returns_1(self):
        assert InferenceProfiler.batch_throughput_factor(0) == 1.0

    def test_batch_2_greater_than_1(self):
        assert InferenceProfiler.batch_throughput_factor(2) > 1.0

    def test_batch_4_greater_than_2(self):
        gain2 = InferenceProfiler.batch_throughput_factor(2)
        gain4 = InferenceProfiler.batch_throughput_factor(4)
        assert gain4 > gain2

    def test_batch_8_greater_than_4(self):
        gain4 = InferenceProfiler.batch_throughput_factor(4)
        gain8 = InferenceProfiler.batch_throughput_factor(8)
        assert gain8 > gain4

    def test_formula_matches_manual(self):
        # T(4) = 4 / (1 + 0.15 × log2(4)) = 4 / (1 + 0.15 × 2) = 4 / 1.3
        expected = 4 / (1 + BATCH_LATENCY_OVERHEAD * 2)
        assert abs(InferenceProfiler.batch_throughput_factor(4) - expected) < 1e-9

    def test_batch_16_is_sublinear(self):
        # True linear scaling would give 16× — we should get less
        gain = InferenceProfiler.batch_throughput_factor(16)
        assert gain < 16.0
        assert gain > 1.0

    def test_large_batch_still_positive(self):
        gain = InferenceProfiler.batch_throughput_factor(128)
        assert gain > 0


# ── TestEdgeCases ─────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_per_task_returns_none_on_zero_tokens(self):
        span = _make_span(input_tokens=0, output_tokens=0)
        assert InferenceProfiler.per_task(span) is None

    def test_per_task_returns_none_below_min_tokens(self):
        span = _make_span(input_tokens=MIN_TOKENS - 1, output_tokens=0)
        assert InferenceProfiler.per_task(span) is None

    def test_per_task_returns_none_on_zero_latency(self):
        span = _make_span(input_tokens=500, output_tokens=200)
        span.worker_start_ts = 0.0
        span.complete_ts = 0.0
        assert InferenceProfiler.per_task(span) is None

    def test_from_spans_returns_none_on_empty(self):
        assert InferenceProfiler.from_spans([]) is None

    def test_from_spans_returns_none_when_all_below_min_tokens(self):
        spans = [_make_span(input_tokens=2, output_tokens=2)] * 5
        result = InferenceProfiler.from_spans(spans)
        assert result is None

    def test_per_task_zero_output_tokens_survives(self):
        # output_tokens=0 means no decode — TPOT should be 0
        span = _make_span(input_tokens=500, output_tokens=0, worker_latency_ms=800.0)
        # total_tokens = 500 < MIN_TOKENS requires MIN_TOKENS to be ≤ 500
        p = InferenceProfiler.per_task(span)
        if p is not None:
            assert p.estimated_tpot_ms == 0.0
            assert p.decode_throughput_tps == 0.0

    def test_single_span_report(self):
        spans = [_make_span(input_tokens=800, output_tokens=200)]
        r = InferenceProfiler.from_spans(spans)
        assert r is not None
        assert r.n_samples == 1

    def test_mixed_models_report(self):
        spans = [
            _make_span(model="deepseek", input_tokens=800, output_tokens=200),
            _make_span(model="sonnet", input_tokens=900, output_tokens=100),
            _make_span(model="deepseek", input_tokens=700, output_tokens=300),
        ]
        r = InferenceProfiler.from_spans(spans)
        assert r is not None
        assert "deepseek" in r.model_prefill_ratios
        assert "sonnet" in r.model_prefill_ratios

    def test_cache_savings_zero_when_no_cost(self):
        span = _make_span(input_tokens=800, output_tokens=200, cost_usd=0.0)
        p = InferenceProfiler.per_task(span, cost_usd=0.0)
        if p is not None:
            assert p.cache_savings_usd == 0.0


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
