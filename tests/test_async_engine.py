"""
Tests for scaffold/agent/core/async_engine.py — Phase 4: Async Systems.

Test classes:
  TestCircuitBreakerClosed   — normal operation, failure recording, rate tracking
  TestCircuitBreakerTrip     — CLOSED → OPEN transition at threshold
  TestCircuitBreakerHalfOpen — OPEN → HALF_OPEN after timeout, probe logic
  TestCircuitBreakerReset    — manual reset, call() wrapper
  TestBackpressureQueue      — put/get, rejection policy, metrics
  TestQueueMetrics           — utilization, rejection rate
  TestAsyncProjection        — speedup math, circuit overhead, backpressure
  TestProjectAsyncGains      — factory function with spans
  TestEdgeCases              — empty inputs, single span, zero workers
"""

import asyncio
import sys
import os
import math
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scaffold.agent.core.async_engine import (
    CircuitBreaker,
    CircuitBreakerState,
    CircuitOpenError,
    BackpressureQueue,
    QueueMetrics,
    AsyncProjection,
    project_async_gains,
    _CIRCUIT_TIMEOUT_S,
    _CIRCUIT_TRIP_COST_FRACTION,
)
from scaffold.agent.core.observability import TaskSpan


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_span(
    success: bool = True,
    total_latency_ms: float = 1200.0,
    input_tokens: int = 800,
    output_tokens: int = 200,
    cost_usd: float = 0.002,
) -> TaskSpan:
    base = time.time()
    return TaskSpan(
        span_id="x",
        task_id="t",
        goal="g",
        action="a",
        file="f.py",
        enqueue_ts=base,
        routing_ts=base + 0.01,
        worker_start_ts=base + 0.02,
        complete_ts=base + total_latency_ms / 1000.0,
        model_chosen="deepseek",
        success=success,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
    )


def _breaker(threshold=0.5, timeout_s=30.0, window=5, min_calls=2) -> CircuitBreaker:
    return CircuitBreaker(
        name="test",
        threshold=threshold,
        timeout_s=timeout_s,
        window=window,
        min_calls=min_calls,
    )


def _run(coro):
    """Run a coroutine synchronously for testing."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ── TestCircuitBreakerClosed ──────────────────────────────────────────────────

class TestCircuitBreakerClosed:
    def test_initial_state_is_closed(self):
        cb = _breaker()
        assert cb.state == CircuitBreakerState.CLOSED

    def test_initial_failure_rate_zero(self):
        cb = _breaker()
        assert cb.failure_rate == 0.0

    def test_record_success_keeps_closed(self):
        cb = _breaker()
        cb.record_success()
        assert cb.state == CircuitBreakerState.CLOSED

    def test_failure_rate_after_mixed_calls(self):
        cb = _breaker(window=4)
        cb.record_success()
        cb.record_success()
        cb.record_failure()
        cb.record_failure()
        assert abs(cb.failure_rate - 0.5) < 1e-9

    def test_failure_rate_sliding_window(self):
        cb = _breaker(window=3)
        # Fill window: S F F
        cb.record_success()
        cb.record_failure()
        cb.record_failure()
        assert abs(cb.failure_rate - 2/3) < 1e-9
        # Slide: F F S — oldest (S) dropped, new S added
        cb.record_success()
        # Window is now: F F S
        assert abs(cb.failure_rate - 2/3) < 1e-9

    def test_not_open_before_min_calls(self):
        cb = _breaker(threshold=0.5, min_calls=5, window=10)
        # 4 failures — below min_calls so should stay CLOSED
        for _ in range(4):
            cb.record_failure()
        assert cb.state == CircuitBreakerState.CLOSED

    def test_total_calls_increments(self):
        cb = _breaker()
        cb.record_success()
        cb.record_failure()
        assert cb.total_calls == 2


# ── TestCircuitBreakerTrip ────────────────────────────────────────────────────

class TestCircuitBreakerTrip:
    def test_opens_when_failure_rate_exceeds_threshold(self):
        cb = _breaker(threshold=0.5, min_calls=2, window=4)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreakerState.OPEN

    def test_trip_count_increments(self):
        cb = _breaker(threshold=0.5, min_calls=2, window=4)
        cb.record_failure()
        cb.record_failure()
        assert cb.total_trips == 1

    def test_rejections_counted_when_open(self):
        cb = _breaker(threshold=0.5, min_calls=2, window=4)
        cb.record_failure()
        cb.record_failure()
        try:
            cb.call(lambda: None)
        except CircuitOpenError:
            pass
        assert cb.total_rejections == 1

    def test_circuit_open_error_is_raised(self):
        cb = _breaker(threshold=0.5, min_calls=2, window=4)
        cb.record_failure()
        cb.record_failure()
        raised = False
        try:
            cb.call(lambda: None)
        except CircuitOpenError:
            raised = True
        assert raised

    def test_is_open_property(self):
        cb = _breaker(threshold=0.5, min_calls=2, window=4)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open is True


# ── TestCircuitBreakerHalfOpen ────────────────────────────────────────────────

class TestCircuitBreakerHalfOpen:
    def _open_breaker(self) -> CircuitBreaker:
        cb = _breaker(threshold=0.5, min_calls=2, window=4, timeout_s=0.0)
        cb.record_failure()
        cb.record_failure()
        return cb

    def test_transitions_to_half_open_after_timeout(self):
        cb = self._open_breaker()
        assert cb.state == CircuitBreakerState.HALF_OPEN

    def test_probe_success_closes_circuit(self):
        cb = self._open_breaker()
        assert cb.state == CircuitBreakerState.HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitBreakerState.CLOSED

    def test_probe_failure_reopens_circuit(self):
        cb = self._open_breaker()
        assert cb.state == CircuitBreakerState.HALF_OPEN
        cb.record_failure()
        # With timeout_s=0.0 the state *property* immediately re-transitions to
        # HALF_OPEN (0.0 s elapsed ≥ 0.0 s timeout). Inspect _state directly to
        # confirm _trip() was called and the circuit did open internally.
        assert cb._state == CircuitBreakerState.OPEN

    def test_second_trip_increments_count(self):
        cb = self._open_breaker()
        assert cb.state == CircuitBreakerState.HALF_OPEN
        cb.record_failure()  # probe fails → reopen
        assert cb.total_trips == 2

    def test_does_not_transition_before_timeout(self):
        cb = _breaker(threshold=0.5, min_calls=2, window=4, timeout_s=9999.0)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreakerState.OPEN  # timeout not elapsed


# ── TestCircuitBreakerReset ───────────────────────────────────────────────────

class TestCircuitBreakerReset:
    def test_reset_closes_circuit(self):
        cb = _breaker(threshold=0.5, min_calls=2, window=4)
        cb.record_failure()
        cb.record_failure()
        cb.reset()
        assert cb.state == CircuitBreakerState.CLOSED

    def test_reset_clears_results(self):
        cb = _breaker(threshold=0.5, min_calls=2, window=4)
        cb.record_failure()
        cb.record_failure()
        cb.reset()
        assert cb.failure_rate == 0.0

    def test_call_fn_success(self):
        cb = _breaker()
        result = cb.call(lambda: 42)
        assert result == 42

    def test_call_fn_failure_records_failure(self):
        cb = _breaker()
        try:
            cb.call(lambda: (_ for _ in ()).throw(ValueError("err")))
        except ValueError:
            pass
        assert cb.failure_rate > 0.0

    def test_status_line_returns_string(self):
        cb = _breaker()
        cb.record_success()
        line = cb.status_line()
        assert "CLOSED" in line
        assert "trips=" in line


# ── TestBackpressureQueue ─────────────────────────────────────────────────────

class TestBackpressureQueue:
    def test_rejects_maxsize_zero(self):
        raised = False
        try:
            BackpressureQueue(maxsize=0)
        except ValueError:
            raised = True
        assert raised

    def test_put_returns_true_when_space(self):
        q = BackpressureQueue(maxsize=5)
        assert q.put("item") is True

    def test_put_returns_false_when_full(self):
        q = BackpressureQueue(maxsize=2)
        q.put("a")
        q.put("b")
        assert q.put("c") is False   # queue full → reject

    def test_depth_increments_on_put(self):
        q = BackpressureQueue(maxsize=5)
        q.put("x")
        q.put("y")
        assert q.depth == 2

    def test_is_empty_initially(self):
        q = BackpressureQueue(maxsize=4)
        assert q.is_empty is True

    def test_is_full_when_at_maxsize(self):
        q = BackpressureQueue(maxsize=2)
        q.put("a")
        q.put("b")
        assert q.is_full is True

    def test_get_retrieves_item(self):
        q = BackpressureQueue(maxsize=4)
        q.put("hello")
        item = _run(q.get())
        assert item == "hello"

    def test_fifo_order(self):
        q = BackpressureQueue(maxsize=5)
        for i in range(3):
            q.put(i)
        retrieved = [_run(q.get()) for _ in range(3)]
        assert retrieved == [0, 1, 2]

    def test_put_wait_async(self):
        async def _test():
            q = BackpressureQueue(maxsize=3)
            await q.put_wait("async_item")
            item = await q.get()
            return item
        assert _run(_test()) == "async_item"

    def test_accepted_count(self):
        q = BackpressureQueue(maxsize=5)
        q.put("a")
        q.put("b")
        assert q.metrics.accepted == 2

    def test_rejected_count(self):
        q = BackpressureQueue(maxsize=1)
        q.put("a")
        q.put("b")  # rejected
        q.put("c")  # rejected
        assert q.metrics.rejected == 2


# ── TestQueueMetrics ──────────────────────────────────────────────────────────

class TestQueueMetrics:
    def test_utilization_calculation(self):
        q = BackpressureQueue(maxsize=4)
        q.put("a")
        q.put("b")
        m = q.metrics
        assert abs(m.utilization - 0.5) < 1e-9

    def test_zero_utilization_on_empty(self):
        q = BackpressureQueue(maxsize=4)
        assert q.metrics.utilization == 0.0

    def test_rejection_rate_zero_when_none_rejected(self):
        q = BackpressureQueue(maxsize=5)
        q.put("a")
        assert q.metrics.rejection_rate == 0.0

    def test_rejection_rate_nonzero(self):
        q = BackpressureQueue(maxsize=1)
        q.put("a")
        q.put("b")  # rejected
        m = q.metrics
        assert abs(m.rejection_rate - 0.5) < 1e-9  # 1 rejected / 2 total

    def test_total_attempted(self):
        q = BackpressureQueue(maxsize=2)
        q.put("a")
        q.put("b")
        q.put("c")  # rejected
        m = q.metrics
        assert m.total_attempted == 3

    def test_display_returns_string(self):
        q = BackpressureQueue(maxsize=4)
        q.put("x")
        text = q.metrics.display()
        assert isinstance(text, str)
        assert "depth=" in text


# ── TestAsyncProjection ───────────────────────────────────────────────────────

class TestAsyncProjection:
    def _proj(
        self,
        n_workers=4,
        n_tasks=8,
        avg_latency_ms=1200.0,
        failure_rate=0.0,
    ) -> AsyncProjection:
        seq = n_tasks * avg_latency_ms
        batches = math.ceil(n_tasks / n_workers)
        ideal = batches * avg_latency_ms
        ideal_speedup = seq / ideal if ideal > 0 else 1.0
        # No circuit overhead when failure_rate=0
        return AsyncProjection(
            n_workers=n_workers,
            n_tasks=n_tasks,
            avg_latency_ms=avg_latency_ms,
            observed_failure_rate=failure_rate,
            sequential_total_ms=seq,
            ideal_parallel_ms=ideal,
            ideal_speedup=ideal_speedup,
            realistic_parallel_ms=ideal,
            realistic_speedup=ideal_speedup,
            expected_circuit_trips=0.0,
            circuit_overhead_ms=0.0,
            queue_maxsize=32,
            predicted_rejection_rate=0.0,
            parallel_cost_usd=0.016,
            potential_cache_savings_usd=0.012,
        )

    def test_sequential_is_n_times_latency(self):
        p = self._proj(n_tasks=8, avg_latency_ms=1200.0)
        assert abs(p.sequential_total_ms - 9600.0) < 1.0

    def test_ideal_speedup_bounded_by_workers(self):
        p = self._proj(n_workers=4, n_tasks=8)
        assert 1.0 < p.ideal_speedup <= 4.0

    def test_ideal_speedup_never_exceeds_n_workers(self):
        for n_workers in [2, 4, 8]:
            p = self._proj(n_workers=n_workers, n_tasks=100)
            assert p.ideal_speedup <= n_workers + 0.001

    def test_realistic_speedup_lte_ideal(self):
        p = self._proj()
        assert p.realistic_speedup <= p.ideal_speedup + 1e-6

    def test_display_returns_string(self):
        p = self._proj()
        text = p.display()
        assert isinstance(text, str)
        assert "ASYNC PROJECTION" in text

    def test_display_contains_key_fields(self):
        p = self._proj()
        text = p.display()
        assert "Sequential" in text
        assert "parallel" in text
        assert "Circuit breaker" in text
        assert "Backpressure" in text


# ── TestProjectAsyncGains ─────────────────────────────────────────────────────

class TestProjectAsyncGains:
    def test_returns_none_on_empty(self):
        assert project_async_gains([]) is None

    def test_returns_projection_with_spans(self):
        spans = [_make_span() for _ in range(8)]
        p = project_async_gains(spans, n_workers=4)
        assert p is not None

    def test_n_tasks_matches_span_count(self):
        spans = [_make_span() for _ in range(10)]
        p = project_async_gains(spans, n_workers=4)
        assert p.n_tasks == 10

    def test_n_workers_field_set(self):
        spans = [_make_span() for _ in range(5)]
        p = project_async_gains(spans, n_workers=3)
        assert p.n_workers == 3

    def test_sequential_total_positive(self):
        spans = [_make_span(total_latency_ms=1000.0)] * 5
        p = project_async_gains(spans, n_workers=2)
        assert p.sequential_total_ms > 0

    def test_ideal_speedup_gt_one_with_multiple_workers(self):
        spans = [_make_span()] * 8
        p = project_async_gains(spans, n_workers=4)
        assert p.ideal_speedup > 1.0

    def test_no_circuit_overhead_all_success(self):
        spans = [_make_span(success=True)] * 10
        p = project_async_gains(spans, n_workers=4)
        assert p.expected_circuit_trips == 0.0
        assert p.circuit_overhead_ms == 0.0

    def test_circuit_overhead_nonzero_when_failures(self):
        # 100% failure rate → circuit trips expected
        spans = [_make_span(success=False)] * 20
        p = project_async_gains(spans, n_workers=4, circuit_threshold=0.5)
        assert p.expected_circuit_trips > 0.0
        assert p.circuit_overhead_ms > 0.0

    def test_realistic_speedup_less_than_ideal_with_failures(self):
        spans = [_make_span(success=False)] * 20
        p = project_async_gains(spans, n_workers=4)
        assert p.realistic_speedup <= p.ideal_speedup

    def test_no_rejection_when_workers_ample(self):
        # 4 workers, slow tasks, low arrival: no backpressure
        spans = [_make_span(total_latency_ms=5000.0)] * 4
        p = project_async_gains(spans, n_workers=8, queue_maxsize=32)
        assert p.predicted_rejection_rate == 0.0

    def test_cache_savings_positive_with_tokens(self):
        spans = [_make_span(input_tokens=800, output_tokens=200, cost_usd=0.005)] * 5
        p = project_async_gains(spans, n_workers=4)
        assert p.potential_cache_savings_usd > 0

    def test_total_cost_is_n_tasks_times_avg(self):
        spans = [_make_span(cost_usd=0.002)] * 5
        p = project_async_gains(spans, n_workers=2)
        assert abs(p.parallel_cost_usd - 0.010) < 1e-9

    def test_ignores_spans_with_zero_latency(self):
        good = [_make_span(total_latency_ms=1000.0)] * 4
        bad = [TaskSpan(span_id="z", task_id="0", goal="", action="", file="")]
        p = project_async_gains(good + bad, n_workers=2)
        assert p is not None
        assert p.n_tasks == 4  # bad span (zero latency) excluded


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
