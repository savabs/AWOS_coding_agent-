"""
Tests for scaffold/agent/core/observability.py — Phase 1: Observability Foundation.

Each test class covers one concept:
  TestTaskSpan        — dataclass fields, derived metrics, serialisation
  TestObsStore        — record / get_recent / metrics aggregation
  TestMetricsSummary  — display output, success_rate property
  TestPercentile      — _percentile edge cases
  TestNewSpan         — factory function stamps enqueue_ts
"""

import json
import tempfile
import time
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scaffold.agent.core.observability import (
    TaskSpan,
    ObservabilityStore,
    MetricsSummary,
    _percentile,
    new_span,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_span(
    task_id="t1",
    success=True,
    enqueue_offset=0.0,
    routing_offset=0.05,
    worker_start_offset=0.10,
    complete_offset=1.10,
    model="deepseek",
    cost=0.002,
    input_tokens=500,
    output_tokens=200,
) -> TaskSpan:
    base = time.time() + enqueue_offset
    span = TaskSpan(
        span_id="abc123",
        task_id=task_id,
        goal="test goal",
        action="add a function",
        file="foo.py",
        enqueue_ts=base,
        routing_ts=base + routing_offset,
        worker_start_ts=base + worker_start_offset,
        complete_ts=base + complete_offset,
        model_chosen=model,
        escalation_level=1,
        strategy="direct",
        attempt_count=1,
        success=success,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost,
    )
    return span


# ── TestTaskSpan ──────────────────────────────────────────────────────────────

class TestTaskSpan:
    def test_queue_wait_ms(self):
        span = _make_span(routing_offset=0.05)
        assert 40 < span.queue_wait_ms < 60

    def test_worker_latency_ms(self):
        span = _make_span(worker_start_offset=0.10, complete_offset=1.10)
        assert 990 < span.worker_latency_ms < 1010

    def test_total_latency_ms(self):
        span = _make_span(enqueue_offset=0, complete_offset=2.0)
        assert 1990 < span.total_latency_ms < 2010

    def test_total_tokens(self):
        span = _make_span(input_tokens=500, output_tokens=200)
        assert span.total_tokens == 700

    def test_cost_per_token(self):
        span = _make_span(cost=0.007, input_tokens=500, output_tokens=200)
        assert abs(span.cost_per_token - 0.007 / 700) < 1e-9

    def test_cost_per_token_zero_tokens(self):
        span = _make_span(input_tokens=0, output_tokens=0, cost=0.001)
        assert span.cost_per_token == 0.0

    def test_to_dict_includes_derived(self):
        span = _make_span()
        d = span.to_dict()
        assert "queue_wait_ms" in d
        assert "worker_latency_ms" in d
        assert "total_latency_ms" in d
        assert "total_tokens" in d
        assert "cost_per_token" in d

    def test_from_dict_roundtrip(self):
        original = _make_span()
        d = original.to_dict()
        restored = TaskSpan.from_dict(d)
        assert restored.task_id == original.task_id
        assert restored.model_chosen == original.model_chosen
        assert restored.success == original.success
        assert abs(restored.cost_usd - original.cost_usd) < 1e-9

    def test_from_dict_ignores_extra_keys(self):
        span = _make_span()
        d = span.to_dict()
        d["nonexistent_field"] = "should be ignored"
        restored = TaskSpan.from_dict(d)
        assert restored.task_id == span.task_id

    def test_one_liner_success(self):
        span = _make_span(success=True, model="deepseek")
        line = span.one_liner()
        assert "✓" in line
        assert "deepseek" in line
        assert "cost=" in line

    def test_one_liner_failure(self):
        span = _make_span(success=False)
        assert "✗" in span.one_liner()

    def test_zero_timestamps_give_zero_latency(self):
        span = TaskSpan(span_id="x", task_id="0", goal="", action="", file="")
        assert span.queue_wait_ms == 0.0
        assert span.worker_latency_ms == 0.0
        assert span.total_latency_ms == 0.0


# ── TestObsStore ──────────────────────────────────────────────────────────────

class TestObsStore:
    def _store(self):
        tmp = tempfile.mkdtemp()
        return ObservabilityStore(persist_dir=tmp)

    def test_record_creates_file(self):
        store = self._store()
        store.record(_make_span())
        assert store._path.exists()

    def test_get_recent_empty(self):
        store = self._store()
        assert store.get_recent(10) == []

    def test_record_and_retrieve(self):
        store = self._store()
        span = _make_span(task_id="abc")
        store.record(span)
        retrieved = store.get_recent(5)
        assert len(retrieved) == 1
        assert retrieved[0].task_id == "abc"

    def test_get_recent_respects_n(self):
        store = self._store()
        for i in range(10):
            store.record(_make_span(task_id=str(i)))
        recent = store.get_recent(3)
        assert len(recent) == 3
        assert recent[-1].task_id == "9"

    def test_multiple_spans_appended(self):
        store = self._store()
        for i in range(5):
            store.record(_make_span(task_id=str(i)))
        all_spans = store.get_recent(100)
        assert len(all_spans) == 5

    def test_metrics_empty(self):
        store = self._store()
        m = store.metrics()
        assert m.total_tasks == 0
        assert m.success_rate == 0.0

    def test_metrics_success_rate(self):
        store = self._store()
        store.record(_make_span(success=True))
        store.record(_make_span(success=True))
        store.record(_make_span(success=False))
        m = store.metrics()
        assert m.success_count == 2
        assert m.failure_count == 1
        assert abs(m.success_rate - 2 / 3) < 0.001

    def test_metrics_model_distribution(self):
        store = self._store()
        store.record(_make_span(model="deepseek"))
        store.record(_make_span(model="deepseek"))
        store.record(_make_span(model="sonnet"))
        m = store.metrics()
        assert m.model_distribution["deepseek"] == 2
        assert m.model_distribution["sonnet"] == 1

    def test_metrics_total_cost(self):
        store = self._store()
        store.record(_make_span(cost=0.001))
        store.record(_make_span(cost=0.003))
        m = store.metrics()
        assert abs(m.total_cost_usd - 0.004) < 1e-9

    def test_metrics_percentile_p50(self):
        store = self._store()
        for offset in [0.1, 0.2, 0.3, 0.4, 0.5]:
            store.record(_make_span(complete_offset=offset))
        m = store.metrics()
        assert m.p50_latency_ms > 0

    def test_skips_corrupt_lines(self):
        store = self._store()
        store.record(_make_span(task_id="good"))
        with store._path.open("a") as f:
            f.write("NOT VALID JSON\n")
        spans = store.get_recent(10)
        assert len(spans) == 1
        assert spans[0].task_id == "good"


# ── TestMetricsSummary ────────────────────────────────────────────────────────

class TestMetricsSummary:
    def _make_summary(self, **kwargs):
        defaults = dict(
            window_size=10, total_tasks=5, success_count=4, failure_count=1,
            p50_latency_ms=800, p95_latency_ms=1500, p99_latency_ms=2000,
            avg_queue_wait_ms=12.5, avg_worker_latency_ms=750,
            throughput_per_min=1.2, avg_cost_usd=0.003, avg_cost_per_token=0.000004,
            total_cost_usd=0.015, model_distribution={"deepseek": 4, "sonnet": 1},
        )
        defaults.update(kwargs)
        return MetricsSummary(**defaults)

    def test_success_rate(self):
        m = self._make_summary(success_count=3, failure_count=1, total_tasks=4)
        assert abs(m.success_rate - 0.75) < 0.001

    def test_success_rate_zero_tasks(self):
        m = self._make_summary(total_tasks=0, success_count=0, failure_count=0)
        assert m.success_rate == 0.0

    def test_display_returns_string(self):
        m = self._make_summary()
        text = m.display()
        assert isinstance(text, str)
        assert "METRICS" in text

    def test_display_contains_key_fields(self):
        m = self._make_summary()
        text = m.display()
        assert "p50" in text
        assert "p95" in text
        assert "p99" in text
        assert "deepseek" in text
        assert "tasks/min" in text


# ── TestPercentile ────────────────────────────────────────────────────────────

class TestPercentile:
    def test_empty_returns_zero(self):
        assert _percentile([], 50) == 0.0

    def test_single_element(self):
        assert _percentile([42.0], 50) == 42.0
        assert _percentile([42.0], 0) == 42.0
        assert _percentile([42.0], 100) == 42.0

    def test_p50_of_sorted_list(self):
        data = [10.0, 20.0, 30.0, 40.0, 50.0]
        assert _percentile(data, 50) == 30.0

    def test_p0_is_min(self):
        data = [5.0, 3.0, 8.0, 1.0]
        assert _percentile(data, 0) == 1.0

    def test_p100_is_max(self):
        data = [5.0, 3.0, 8.0, 1.0]
        assert _percentile(data, 100) == 8.0

    def test_p95_interpolation(self):
        data = list(range(1, 101))  # 1..100
        result = _percentile([float(x) for x in data], 95)
        assert 94.0 <= result <= 96.0


# ── TestNewSpan ───────────────────────────────────────────────────────────────

class TestNewSpan:
    def test_stamps_enqueue_ts(self):
        before = time.time()
        span = new_span(task_id=1, goal="g", action="a", file="f.py")
        after = time.time()
        assert before <= span.enqueue_ts <= after

    def test_fields_set(self):
        span = new_span(task_id=42, goal="my goal", action="do X", file="bar.py")
        assert span.task_id == "42"
        assert span.goal == "my goal"
        assert span.action == "do X"
        assert span.file == "bar.py"

    def test_span_id_is_hex_string(self):
        span = new_span(task_id=1, goal="", action="", file="")
        assert len(span.span_id) == 12
        int(span.span_id, 16)  # raises ValueError if not hex


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
