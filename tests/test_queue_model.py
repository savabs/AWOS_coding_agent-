"""
Tests for scaffold/agent/core/queue_model.py — Phase 2: Queueing Theory + Regret.

Test classes:
  TestMM1Queue       — M/M/1 parameter estimation, stability, saturation
  TestQueueState     — derived properties, display output
  TestRegretTracker  — pseudo-regret computation, oracle selection, bound
  TestEdgeCases      — empty inputs, single spans, unstable queue
"""

import sys
import os
import math
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scaffold.agent.core.queue_model import (
    MM1Queue,
    QueueState,
    RegretTracker,
    RegretState,
    ACTION_NAMES,
)
from scaffold.agent.core.observability import TaskSpan


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_span(
    enqueue_ts: float,
    worker_latency_ms: float = 1000.0,
    queue_wait_ms_hint: float = 10.0,
    success: bool = True,
) -> TaskSpan:
    """Build a synthetic TaskSpan with realistic timestamps."""
    routing_ts = enqueue_ts + queue_wait_ms_hint / 1000.0
    worker_start_ts = routing_ts
    complete_ts = worker_start_ts + worker_latency_ms / 1000.0
    return TaskSpan(
        span_id="x",
        task_id="t",
        goal="g",
        action="a",
        file="f.py",
        enqueue_ts=enqueue_ts,
        routing_ts=routing_ts,
        worker_start_ts=worker_start_ts,
        complete_ts=complete_ts,
        model_chosen="deepseek",
        success=success,
    )


def _make_episode(action_id: int, reward: float):
    """Minimal episode stub for RegretTracker tests."""
    class _Ep:
        pass
    ep = _Ep()
    ep.action_id = action_id
    ep.reward = reward
    return ep


def _make_spans_mm1(
    n: int = 20,
    inter_arrival_s: float = 2.0,
    service_ms: float = 1000.0,
) -> list:
    """Generate n spans with uniform inter-arrival and service times."""
    base = time.time()
    spans = []
    for i in range(n):
        enqueue = base + i * inter_arrival_s
        spans.append(_make_span(
            enqueue_ts=enqueue,
            worker_latency_ms=service_ms,
            queue_wait_ms_hint=5.0,
        ))
    return spans


# ── TestMM1Queue ──────────────────────────────────────────────────────────────

class TestMM1Queue:
    def test_returns_none_below_min_samples(self):
        spans = _make_spans_mm1(n=2)
        assert MM1Queue.from_spans(spans) is None

    def test_returns_state_with_enough_samples(self):
        spans = _make_spans_mm1(n=10)
        state = MM1Queue.from_spans(spans)
        assert state is not None

    def test_utilization_is_ratio_of_rates(self):
        # inter_arrival=2s → λ≈0.5/s; service=500ms → μ≈2/s; ρ≈0.25
        spans = _make_spans_mm1(n=20, inter_arrival_s=2.0, service_ms=500.0)
        state = MM1Queue.from_spans(spans)
        assert state is not None
        assert abs(state.utilization - state.arrival_rate / state.service_rate) < 1e-9

    def test_stable_queue(self):
        # λ=0.5, μ=2.0 → ρ=0.25 < 1 → stable
        spans = _make_spans_mm1(n=20, inter_arrival_s=2.0, service_ms=500.0)
        state = MM1Queue.from_spans(spans)
        assert state is not None
        assert state.is_stable is True

    def test_unstable_queue(self):
        # λ=2.0, μ=0.5 → ρ=4.0 > 1 → unstable
        spans = _make_spans_mm1(n=20, inter_arrival_s=0.5, service_ms=2000.0)
        state = MM1Queue.from_spans(spans)
        assert state is not None
        assert state.is_stable is False

    def test_theoretical_wq_positive_when_stable(self):
        spans = _make_spans_mm1(n=20, inter_arrival_s=2.0, service_ms=500.0)
        state = MM1Queue.from_spans(spans)
        assert state is not None and state.is_stable
        assert state.theoretical_queue_wait_ms >= 0

    def test_theoretical_wq_infinite_when_unstable(self):
        spans = _make_spans_mm1(n=20, inter_arrival_s=0.5, service_ms=2000.0)
        state = MM1Queue.from_spans(spans)
        assert state is not None and not state.is_stable
        assert math.isinf(state.theoretical_queue_wait_ms)

    def test_little_law_holds_approximately(self):
        # L = λ · W
        spans = _make_spans_mm1(n=30, inter_arrival_s=3.0, service_ms=500.0)
        state = MM1Queue.from_spans(spans)
        assert state is not None and state.is_stable
        w_s = state.theoretical_system_time_ms / 1000.0
        expected_l = state.arrival_rate * w_s
        assert abs(state.mean_system_length - expected_l) < 0.01

    def test_lq_equals_rho_squared_over_one_minus_rho(self):
        spans = _make_spans_mm1(n=20, inter_arrival_s=2.0, service_ms=500.0)
        state = MM1Queue.from_spans(spans)
        assert state is not None and state.is_stable
        rho = state.utilization
        expected_lq = rho ** 2 / (1.0 - rho)
        assert abs(state.mean_queue_length - expected_lq) < 0.001

    def test_n_samples_matches_valid_span_count(self):
        spans = _make_spans_mm1(n=15)
        state = MM1Queue.from_spans(spans)
        assert state is not None
        assert state.n_samples == 15

    def test_arrival_rate_approximately_correct(self):
        # 20 spans, 2s apart → λ ≈ 0.5/s
        spans = _make_spans_mm1(n=20, inter_arrival_s=2.0)
        state = MM1Queue.from_spans(spans)
        assert state is not None
        assert abs(state.arrival_rate - 0.5) < 0.05

    def test_service_rate_approximately_correct(self):
        # service_ms=1000ms → μ ≈ 1.0/s
        spans = _make_spans_mm1(n=20, inter_arrival_s=5.0, service_ms=1000.0)
        state = MM1Queue.from_spans(spans)
        assert state is not None
        assert abs(state.service_rate - 1.0) < 0.05


# ── TestQueueState ────────────────────────────────────────────────────────────

class TestQueueState:
    def _stable_state(self) -> QueueState:
        spans = _make_spans_mm1(n=20, inter_arrival_s=2.0, service_ms=500.0)
        return MM1Queue.from_spans(spans)

    def test_saturation_pct(self):
        state = self._stable_state()
        assert abs(state.saturation_pct - state.utilization * 100) < 0.001

    def test_no_saturation_warning_when_low(self):
        state = self._stable_state()
        assert state.utilization < 0.80
        assert state.saturation_warning is None

    def test_saturation_warning_when_high(self):
        # λ=0.9, μ=1.0 → ρ=0.9 > 0.8 → warning
        spans = _make_spans_mm1(n=20, inter_arrival_s=1.0/0.9, service_ms=1000.0)
        state = MM1Queue.from_spans(spans)
        assert state is not None and state.is_stable
        if state.utilization >= 0.80:
            assert state.saturation_warning is not None

    def test_display_returns_string(self):
        state = self._stable_state()
        text = state.display()
        assert isinstance(text, str)
        assert "M/M/1" in text

    def test_display_contains_key_metrics(self):
        state = self._stable_state()
        text = state.display()
        assert "ρ=" in text
        assert "Wq=" in text
        assert "λ=" in text
        assert "μ=" in text

    def test_gap_is_theory_minus_actual(self):
        state = self._stable_state()
        expected = state.theoretical_queue_wait_ms - state.actual_queue_wait_ms
        assert abs(state.model_vs_reality_gap_ms - expected) < 1e-9


# ── TestRegretTracker ─────────────────────────────────────────────────────────

class TestRegretTracker:
    def test_returns_none_on_empty(self):
        assert RegretTracker.from_episodes([]) is None

    def test_returns_state_with_episodes(self):
        eps = [_make_episode(1, 0.9) for _ in range(5)]
        state = RegretTracker.from_episodes(eps)
        assert state is not None

    def test_total_episodes_count(self):
        eps = [_make_episode(1, 0.8)] * 7
        state = RegretTracker.from_episodes(eps)
        assert state.total_episodes == 7

    def test_oracle_is_best_action(self):
        # action 4 (sonnet) always gets 0.9, others get 0.1
        eps = (
            [_make_episode(0, 0.1)] * 5 +
            [_make_episode(1, 0.1)] * 5 +
            [_make_episode(4, 0.9)] * 5
        )
        state = RegretTracker.from_episodes(eps)
        assert state.oracle_action_id == 4
        assert state.oracle_action_name == "sonnet"
        assert abs(state.oracle_mean_reward - 0.9) < 0.01

    def test_cumulative_regret_is_nonnegative(self):
        eps = [_make_episode(i % 5, 0.5) for i in range(20)]
        state = RegretTracker.from_episodes(eps)
        assert state.cumulative_regret >= 0.0

    def test_zero_regret_when_single_action_used(self):
        # Only one action → no regret (oracle = only choice)
        eps = [_make_episode(1, 0.8)] * 10
        state = RegretTracker.from_episodes(eps)
        # Regret should be ~0 (oracle = deepseek = only arm tried; fp noise tolerated)
        assert state.cumulative_regret < 1e-10

    def test_avg_regret_per_episode(self):
        eps = [_make_episode(1, 0.5)] * 10
        state = RegretTracker.from_episodes(eps)
        assert abs(state.avg_regret_per_episode - state.cumulative_regret / 10) < 1e-9

    def test_action_mean_rewards_populated(self):
        eps = [_make_episode(0, 0.5), _make_episode(1, 0.7)]
        state = RegretTracker.from_episodes(eps)
        assert "deepseek" in state.action_mean_rewards
        assert "gemini_flash" in state.action_mean_rewards

    def test_theoretical_bound_grows_with_t(self):
        state_small = RegretTracker.from_episodes([_make_episode(0, 0.5)] * 10)
        state_large = RegretTracker.from_episodes([_make_episode(0, 0.5)] * 100)
        assert state_large.theoretical_bound > state_small.theoretical_bound

    def test_display_returns_string(self):
        eps = [_make_episode(i % 5, 0.5 + i * 0.01) for i in range(20)]
        state = RegretTracker.from_episodes(eps)
        text = state.display()
        assert isinstance(text, str)
        assert "Cumulative regret" in text

    def test_display_contains_oracle_info(self):
        eps = [_make_episode(1, 0.9)] * 5
        state = RegretTracker.from_episodes(eps)
        assert "Oracle" in state.display()

    def test_regret_vs_bound_ratio(self):
        eps = [_make_episode(0, 0.5)] * 50
        state = RegretTracker.from_episodes(eps)
        expected = state.cumulative_regret / state.theoretical_bound
        assert abs(state.regret_vs_bound_ratio - expected) < 1e-9


# ── TestEdgeCases ─────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_mm1_empty_list(self):
        assert MM1Queue.from_spans([]) is None

    def test_mm1_one_span(self):
        spans = [_make_span(enqueue_ts=time.time())]
        assert MM1Queue.from_spans(spans) is None

    def test_mm1_two_spans(self):
        # 2 spans < _MIN_SAMPLES (3) → None
        base = time.time()
        spans = [
            _make_span(enqueue_ts=base),
            _make_span(enqueue_ts=base + 1.0),
        ]
        assert MM1Queue.from_spans(spans) is None

    def test_mm1_three_spans_returns_state(self):
        base = time.time()
        spans = [_make_span(enqueue_ts=base + i * 2.0) for i in range(3)]
        result = MM1Queue.from_spans(spans)
        assert result is not None

    def test_regret_single_episode(self):
        eps = [_make_episode(2, 0.7)]
        state = RegretTracker.from_episodes(eps)
        assert state is not None
        assert state.total_episodes == 1

    def test_mm1_ignores_spans_with_zero_timestamps(self):
        base = time.time()
        good = [_make_span(enqueue_ts=base + i * 2.0) for i in range(5)]
        bad = [TaskSpan(span_id="z", task_id="0", goal="", action="", file="")]  # zeros
        result = MM1Queue.from_spans(good + bad)
        assert result is not None
        assert result.n_samples == 5  # bad span excluded


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
