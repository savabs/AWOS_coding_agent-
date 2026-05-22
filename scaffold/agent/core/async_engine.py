"""
Phase 4: Async Systems — Circuit Breaker, Backpressure, Async Projection

Three patterns that every production async system needs:

┌─ CIRCUIT BREAKER ───────────────────────────────────────────────────────────┐
│ Prevents cascading failure when a downstream service is unhealthy.           │
│                                                                              │
│ State machine:                                                               │
│   CLOSED ──[failure_rate > threshold]──► OPEN                               │
│     ▲                                     │                                  │
│     │                      [timeout elapsed]                                 │
│     │                                     ▼                                  │
│     ├──[probe succeeds]────── HALF_OPEN                                      │
│     └──[probe fails]────────────────────► OPEN (reset timer)                │
│                                                                              │
│ CLOSED:    All calls pass through. Track success/failure in rolling window.  │
│ OPEN:      All calls rejected instantly (fail fast). No wasted timeout wait. │
│ HALF_OPEN: One probe request allowed. Decision based on outcome.             │
│                                                                              │
│ Why it matters for AWOS: LLM APIs have rate limits and transient 5xx errors. │
│ Without a breaker, a failing API causes workers to hang on timeout (30s+)    │
│ per task. With a breaker, they fail in <1ms and can retry with a cheaper     │
│ tier immediately.                                                             │
│                                                                              │
│ Reference: M. Nygard, Release It! (2007). Ch. 5: Stability Patterns.        │
└──────────────────────────────────────────────────────────────────────────────┘

┌─ BACKPRESSURE QUEUE ────────────────────────────────────────────────────────┐
│ When producers are faster than consumers, the queue grows without bound.     │
│ Backpressure = the queue sends a "slow down" signal to producers.            │
│                                                                              │
│ Three policies:                                                              │
│   BLOCK:    producer.put() blocks until space is available (asyncio default) │
│   REJECT:   put() returns False immediately; caller handles rejection        │
│   DROP_OLD: evict oldest item to make room (useful for real-time streams)    │
│                                                                              │
│ AWOS uses REJECT: a full queue means the system is saturated, not that       │
│ the task should wait indefinitely. Rejected tasks are logged and can be      │
│ retried in the next planning cycle.                                          │
│                                                                              │
│ asyncio.Queue(maxsize=N) provides O(1) put/get with FIFO ordering.           │
│ When maxsize=0, the queue is unbounded (dangerous in production).            │
└──────────────────────────────────────────────────────────────────────────────┘

┌─ ASYNC PROJECTION ──────────────────────────────────────────────────────────┐
│ Predicts what N-worker async execution would achieve vs current sequential.  │
│                                                                              │
│ For I/O-bound workloads (LLM API calls): most time is spent waiting for the  │
│ network, not CPU. N concurrent workers can overlap that waiting:             │
│                                                                              │
│   Sequential:   ──[t1]──[t2]──[t3]──[t4]──  total = N × latency            │
│   4 workers:    ──[t1]──[t5]──  total ≈ ceil(N/4) × latency                 │
│                 ──[t2]──[t6]──  (4× faster, ideal case)                     │
│                 ──[t3]──[t7]──                                               │
│                 ──[t4]──[t8]──                                               │
│                                                                              │
│ Actual gain is reduced by:                                                   │
│   - Circuit breaker trips (failed tasks stall one worker slot)               │
│   - Queue overhead (put/get coordination)                                    │
│   - API rate-limiting (concurrency limits per provider)                      │
│                                                                              │
│ Typical observed gain for LLM API workloads: 3-5× at 4-8 workers.           │
└──────────────────────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Deque, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .observability import TaskSpan


# ── Circuit Breaker ───────────────────────────────────────────────────────────

class CircuitBreakerState(Enum):
    CLOSED    = "CLOSED"       # normal operation
    OPEN      = "OPEN"         # failing fast
    HALF_OPEN = "HALF_OPEN"    # probing recovery


class CircuitOpenError(Exception):
    """Raised when a call is attempted while the circuit is OPEN."""
    def __init__(self, breaker_name: str, failure_rate: float, opens_at: float):
        self.breaker_name = breaker_name
        self.failure_rate = failure_rate
        self.opens_at = opens_at
        super().__init__(
            f"Circuit '{breaker_name}' is OPEN "
            f"(failure_rate={failure_rate:.2%}, opened {time.time()-opens_at:.1f}s ago)"
        )


class CircuitBreaker:
    """
    Sync-compatible circuit breaker with a rolling window failure tracker.

    Works in both sync and async code — does not require await.

    Parameters
    ----------
    name          : identifier for logging
    threshold     : failure rate in [0, 1] that triggers OPEN (default 0.5 = 50%)
    timeout_s     : seconds to wait in OPEN before trying HALF_OPEN (default 30)
    window        : rolling window size for failure rate calculation (default 10)
    min_calls     : minimum calls before threshold is enforced (default 3)
    """

    def __init__(
        self,
        name: str = "default",
        threshold: float = 0.5,
        timeout_s: float = 30.0,
        window: int = 10,
        min_calls: int = 3,
    ) -> None:
        self.name = name
        self.threshold = threshold
        self.timeout_s = timeout_s
        self.window = window
        self.min_calls = min_calls

        self._state = CircuitBreakerState.CLOSED
        self._results: Deque[bool] = deque(maxlen=window)   # True=success, False=failure
        self._opened_at: float = 0.0
        self._probe_in_flight: bool = False      # only one probe at a time in HALF_OPEN

        # Metrics
        self.total_calls: int = 0
        self.total_rejections: int = 0           # calls rejected due to OPEN state
        self.total_trips: int = 0                # times circuit opened

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def state(self) -> CircuitBreakerState:
        self._maybe_transition_to_half_open()
        return self._state

    @property
    def failure_rate(self) -> float:
        """Rolling failure rate over the last `window` calls."""
        if not self._results:
            return 0.0
        return self._results.count(False) / len(self._results)

    @property
    def is_open(self) -> bool:
        return self.state == CircuitBreakerState.OPEN

    def call(self, fn: Callable, *args: Any, **kwargs: Any) -> Any:
        """
        Execute fn(*args, **kwargs) through the circuit breaker.

        Raises CircuitOpenError if state is OPEN.
        Records the outcome (success/failure) automatically.
        Any exception from fn is treated as a failure and re-raised.
        """
        self._check_open()
        self.total_calls += 1
        try:
            result = fn(*args, **kwargs)
            self._record(True)
            return result
        except Exception:
            self._record(False)
            raise

    def record_success(self) -> None:
        """Manually record a success (for cases where call() cannot be used)."""
        self.total_calls += 1
        self._record(True)

    def record_failure(self) -> None:
        """Manually record a failure."""
        self.total_calls += 1
        self._record(False)

    def reset(self) -> None:
        """Manually reset to CLOSED. Useful in tests or after a forced recovery."""
        self._state = CircuitBreakerState.CLOSED
        self._results.clear()
        self._opened_at = 0.0
        self._probe_in_flight = False

    def status_line(self) -> str:
        rate_str = f"{self.failure_rate:.0%}" if self._results else "—"
        return (
            f"circuit={self.state.value}  "
            f"failure_rate={rate_str}  "
            f"trips={self.total_trips}  "
            f"rejections={self.total_rejections}"
        )

    # ── Internal ──────────────────────────────────────────────────────────────

    def _check_open(self) -> None:
        """Raise CircuitOpenError if state is OPEN (after transition check)."""
        if self.state == CircuitBreakerState.OPEN:
            self.total_rejections += 1
            raise CircuitOpenError(self.name, self.failure_rate, self._opened_at)

        if self.state == CircuitBreakerState.HALF_OPEN:
            if self._probe_in_flight:
                # Only one probe at a time — reject concurrent callers
                self.total_rejections += 1
                raise CircuitOpenError(self.name, self.failure_rate, self._opened_at)
            self._probe_in_flight = True

    def _record(self, success: bool) -> None:
        """Record outcome and update state."""
        self._results.append(success)

        if self._state == CircuitBreakerState.HALF_OPEN:
            self._probe_in_flight = False
            if success:
                self._state = CircuitBreakerState.CLOSED
            else:
                self._trip()
            return

        # CLOSED: check if we should trip
        if (
            self._state == CircuitBreakerState.CLOSED
            and len(self._results) >= self.min_calls
            and self.failure_rate > self.threshold
        ):
            self._trip()

    def _trip(self) -> None:
        """Transition to OPEN."""
        self._state = CircuitBreakerState.OPEN
        self._opened_at = time.time()
        self.total_trips += 1

    def _maybe_transition_to_half_open(self) -> None:
        """Transition OPEN → HALF_OPEN when timeout has elapsed."""
        if (
            self._state == CircuitBreakerState.OPEN
            and time.time() - self._opened_at >= self.timeout_s
        ):
            self._state = CircuitBreakerState.HALF_OPEN
            self._probe_in_flight = False


# ── Backpressure Queue ────────────────────────────────────────────────────────

@dataclass
class QueueMetrics:
    """Snapshot of BackpressureQueue health."""
    maxsize: int
    current_depth: int
    accepted: int
    rejected: int
    total_attempted: int

    @property
    def utilization(self) -> float:
        return self.current_depth / self.maxsize if self.maxsize > 0 else 0.0

    @property
    def rejection_rate(self) -> float:
        return self.rejected / self.total_attempted if self.total_attempted > 0 else 0.0

    def display(self) -> str:
        return (
            f"queue depth={self.current_depth}/{self.maxsize} "
            f"({self.utilization:.0%})  "
            f"rejected={self.rejected}/{self.total_attempted} "
            f"({self.rejection_rate:.1%} rejection rate)"
        )


class BackpressureQueue:
    """
    asyncio.Queue wrapper that implements REJECT backpressure.

    When the queue is full, `put()` returns False immediately instead of
    blocking. This signals the producer to slow down or defer the task.

    Parameters
    ----------
    maxsize : maximum number of items in flight (0 = unbounded, not recommended)
    """

    def __init__(self, maxsize: int = 32) -> None:
        if maxsize <= 0:
            raise ValueError(
                f"maxsize must be > 0 for backpressure to work. Got {maxsize}. "
                "Use asyncio.Queue() directly for unbounded queues."
            )
        self._q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._maxsize = maxsize
        self._accepted: int = 0
        self._rejected: int = 0

    # ── Producer API ──────────────────────────────────────────────────────────

    def put(self, item: Any) -> bool:
        """
        Non-blocking put with REJECT policy.

        Returns True if the item was queued, False if the queue is full.
        Does NOT raise — callers check the return value.

        Note: Intentionally synchronous so it can be called from non-async code.
        For async callers that want to await space, use put_wait() instead.
        """
        try:
            self._q.put_nowait(item)
            self._accepted += 1
            return True
        except asyncio.QueueFull:
            self._rejected += 1
            return False

    async def put_wait(self, item: Any) -> None:
        """
        Async put that BLOCKS until space is available.
        Use this when you want the producer to naturally slow down.
        """
        await self._q.put(item)
        self._accepted += 1

    # ── Consumer API ──────────────────────────────────────────────────────────

    async def get(self) -> Any:
        """Async get — blocks until an item is available."""
        return await self._q.get()

    def task_done(self) -> None:
        """Signal that a previously dequeued item has been processed."""
        self._q.task_done()

    async def join(self) -> None:
        """Block until all items have been processed (task_done called for each)."""
        await self._q.join()

    # ── Metrics ───────────────────────────────────────────────────────────────

    @property
    def depth(self) -> int:
        return self._q.qsize()

    @property
    def is_full(self) -> bool:
        return self._q.full()

    @property
    def is_empty(self) -> bool:
        return self._q.empty()

    @property
    def metrics(self) -> QueueMetrics:
        return QueueMetrics(
            maxsize=self._maxsize,
            current_depth=self.depth,
            accepted=self._accepted,
            rejected=self._rejected,
            total_attempted=self._accepted + self._rejected,
        )


# ── Async Projection ──────────────────────────────────────────────────────────

@dataclass
class AsyncProjection:
    """
    Predicted gains from switching AWOS to N-worker async execution.

    Based on actual observed spans (Phase 1 data). No simulation — pure
    queuing math applied to empirical measurements.

    Key formula:
        Sequential time   = n_tasks × avg_latency
        Parallel time     ≈ ceil(n_tasks / n_workers) × avg_latency  (ideal)
        Ideal speedup     = n_tasks / ceil(n_tasks / n_workers)

    Degradations applied:
        - Circuit breaker overhead: failed tasks block a worker slot for
          circuit_timeout_s until the probe succeeds. Each trip wastes
          roughly timeout_s / avg_latency task-slots.
        - Queue backpressure: if arrival rate > service rate, some tasks
          are rejected and must be retried in the next cycle.
    """

    n_workers: int
    n_tasks: int

    # Observed baseline (Phase 1)
    avg_latency_ms: float
    observed_failure_rate: float

    # Sequential baseline
    sequential_total_ms: float       # n_tasks × avg_latency

    # Ideal parallel (no overhead)
    ideal_parallel_ms: float         # ceil(n_tasks / n_workers) × avg_latency
    ideal_speedup: float             # sequential / ideal_parallel

    # Realistic parallel (with circuit breaker + queue overhead)
    realistic_parallel_ms: float
    realistic_speedup: float

    # Circuit breaker prediction
    expected_circuit_trips: float    # estimated trips for this batch
    circuit_overhead_ms: float       # time lost to open-circuit timeouts

    # Queue backpressure prediction
    queue_maxsize: int
    predicted_rejection_rate: float  # tasks rejected if arrival faster than processing

    # Cost implication (from Phase 3)
    parallel_cost_usd: float         # same total cost (API calls don't get cheaper)
    potential_cache_savings_usd: float

    def display(self) -> str:
        ideal_str = f"{self.ideal_speedup:.1f}×"
        real_str = f"{self.realistic_speedup:.1f}×"
        trip_str = f"~{self.expected_circuit_trips:.1f}" if self.expected_circuit_trips >= 0.1 else "unlikely"
        rejection_str = f"{self.predicted_rejection_rate:.1%}"

        return "\n".join([
            f"┌─ ASYNC PROJECTION  ({self.n_workers} workers, {self.n_tasks} tasks) ──────────────────",
            f"│  Sequential        {self.sequential_total_ms/1000:.1f}s  (baseline)",
            f"│  Ideal parallel    {self.ideal_parallel_ms/1000:.1f}s  ({ideal_str} speedup, no overhead)",
            f"│  Realistic parallel {self.realistic_parallel_ms/1000:.1f}s  ({real_str} speedup, with overhead)",
            f"│  Circuit breaker   trips={trip_str}  overhead={self.circuit_overhead_ms:.0f}ms  "
            f"failure_rate={self.observed_failure_rate:.0%}",
            f"│  Backpressure      queue_size={self.queue_maxsize}  "
            f"rejection_rate={rejection_str} at peak load",
            f"│  Cost              ${self.parallel_cost_usd:.4f} total  "
            f"${self.potential_cache_savings_usd:.4f} cache savings available",
            f"└────────────────────────────────────────────────────────────────",
        ])


# ── Projection factory ────────────────────────────────────────────────────────

# Fraction of circuit timeout consumed on average per trip
# (usually timeout_s × fraction until probe succeeds)
_CIRCUIT_TIMEOUT_S = 30.0    # default CircuitBreaker timeout
_CIRCUIT_TRIP_COST_FRACTION = 0.5   # expect probe to succeed at ~halfway through timeout


def project_async_gains(
    spans: List["TaskSpan"],
    n_workers: int = 4,
    queue_maxsize: int = 32,
    circuit_threshold: float = 0.5,
) -> Optional[AsyncProjection]:
    """
    Compute AsyncProjection from observed TaskSpans.

    Parameters
    ----------
    spans           : recent spans from ObservabilityStore
    n_workers       : number of hypothetical concurrent workers
    queue_maxsize   : backpressure queue capacity
    circuit_threshold : failure rate that opens the circuit
    """
    valid = [s for s in spans if s.total_latency_ms > 0]
    if not valid:
        return None

    n_tasks = len(valid)
    avg_lat = sum(s.total_latency_ms for s in valid) / n_tasks
    success_count = sum(1 for s in valid if s.success)
    failure_rate = 1.0 - (success_count / n_tasks)
    avg_cost = sum(s.cost_usd for s in valid) / n_tasks

    # ── Sequential baseline ───────────────────────────────────────────────────
    seq_ms = n_tasks * avg_lat

    # ── Ideal parallel ────────────────────────────────────────────────────────
    batches = math.ceil(n_tasks / n_workers)
    ideal_ms = batches * avg_lat
    ideal_speedup = seq_ms / ideal_ms if ideal_ms > 0 else 1.0

    # ── Circuit breaker overhead ──────────────────────────────────────────────
    # Expected trips: if failure_rate > threshold, the circuit opens roughly
    # every (min_calls / failure_rate) calls on average.
    # For a batch of n_tasks tasks, number of expected trips:
    min_calls = 3
    if failure_rate > circuit_threshold and failure_rate > 0:
        calls_per_trip = min_calls / failure_rate
        expected_trips = n_tasks / calls_per_trip
    else:
        expected_trips = 0.0

    # Each trip: workers blocked for ~timeout_s × fraction before probe
    circuit_overhead_ms = expected_trips * _CIRCUIT_TIMEOUT_S * _CIRCUIT_TRIP_COST_FRACTION * 1000.0

    # ── Realistic parallel time ───────────────────────────────────────────────
    # Add circuit overhead distributed across parallel execution
    realistic_ms = ideal_ms + circuit_overhead_ms / n_workers
    realistic_speedup = seq_ms / realistic_ms if realistic_ms > 0 else 1.0

    # ── Backpressure rejection prediction ─────────────────────────────────────
    # At steady state, if tasks arrive faster than workers process them:
    # arrival_rate = n_tasks / seq_ms (sequential submission rate)
    # service_rate = n_workers / avg_lat (parallel processing rate)
    # If arrival_rate > service_rate → queue fills → rejections occur
    arrival_rate = n_tasks / seq_ms if seq_ms > 0 else 0.0
    service_rate = n_workers / avg_lat if avg_lat > 0 else 0.0
    rho_async = arrival_rate / service_rate if service_rate > 0 else 0.0
    # Rejection rate ≈ max(0, ρ - 1) / ρ (tasks beyond capacity)
    predicted_rejection_rate = max(0.0, (rho_async - 1.0) / rho_async) if rho_async > 1.0 else 0.0

    # ── Cost (parallel doesn't change per-call cost) ──────────────────────────
    total_cost = avg_cost * n_tasks
    # Cache savings: prefill-heavy workload benefit from prompt caching
    avg_inp = sum(s.input_tokens for s in valid) / n_tasks
    avg_total = sum(s.total_tokens for s in valid) / n_tasks
    prefill_ratio = avg_inp / avg_total if avg_total > 0 else 0.0
    cache_savings = total_cost * prefill_ratio * 0.90  # 90% prefill cost saved

    return AsyncProjection(
        n_workers=n_workers,
        n_tasks=n_tasks,
        avg_latency_ms=avg_lat,
        observed_failure_rate=failure_rate,
        sequential_total_ms=seq_ms,
        ideal_parallel_ms=ideal_ms,
        ideal_speedup=ideal_speedup,
        realistic_parallel_ms=realistic_ms,
        realistic_speedup=realistic_speedup,
        expected_circuit_trips=expected_trips,
        circuit_overhead_ms=circuit_overhead_ms,
        queue_maxsize=queue_maxsize,
        predicted_rejection_rate=predicted_rejection_rate,
        parallel_cost_usd=total_cost,
        potential_cache_savings_usd=cache_savings,
    )
