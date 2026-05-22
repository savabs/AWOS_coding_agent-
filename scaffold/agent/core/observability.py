"""
Phase 1: Observability Foundation

Every task execution leaves a structured TaskSpan — a complete record of timing,
routing decisions, and outcome. ObservabilityStore persists these to a JSONL file.
MetricsSummary aggregates spans into real statistics: latency percentiles, throughput,
model distribution, cost efficiency.

Latency anatomy:
    enqueue_ts ──[queue_wait]──► routing_ts ──[routing_to_worker]──► worker_start_ts
    worker_start_ts ──────────────────[worker_latency]─────────────► complete_ts
    enqueue_ts ──────────────────────────[total_latency]────────────► complete_ts

Concepts implemented:
    - Latency decomposition: separate queue wait from compute time
    - Percentile latency: p50 / p95 / p99 from a sliding window (no scipy needed)
    - Throughput: tasks / elapsed minutes over the observation window
    - Model distribution: which tiers actually run and how often
    - Cost efficiency: USD per task and USD per million tokens
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# ── Percentile helper (stdlib only) ──────────────────────────────────────────

def _percentile(data: List[float], p: float) -> float:
    """Linear-interpolation percentile. Returns 0.0 on empty input."""
    if not data:
        return 0.0
    s = sorted(data)
    k = (len(s) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (k - lo) * (s[hi] - s[lo])


# ── TaskSpan ──────────────────────────────────────────────────────────────────

@dataclass
class TaskSpan:
    """
    Immutable trace for one task execution.

    Fields are set in three stages:
        Stage 1 – enqueue:  span_id, task_id, goal, action, file, enqueue_ts
        Stage 2 – routing:  routing_ts, model_chosen, escalation_level, strategy
        Stage 3 – complete: worker_start_ts, complete_ts, success, attempt_count,
                            input_tokens, output_tokens, cost_usd
    """

    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    task_id: str = ""
    goal: str = ""
    action: str = ""
    file: str = ""

    # Timestamps — unix epoch floats
    enqueue_ts: float = 0.0
    routing_ts: float = 0.0
    worker_start_ts: float = 0.0
    complete_ts: float = 0.0

    # Routing decision
    model_chosen: str = "unknown"
    escalation_level: int = 0
    strategy: str = "unknown"
    attempt_count: int = 1

    # Outcome
    success: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    # ── Derived metrics (computed, not stored) ────────────────────────────

    @property
    def queue_wait_ms(self) -> float:
        """Time from task arrival to routing decision."""
        if self.routing_ts and self.enqueue_ts:
            return (self.routing_ts - self.enqueue_ts) * 1000.0
        return 0.0

    @property
    def worker_latency_ms(self) -> float:
        """Time from first worker call to task completion."""
        if self.complete_ts and self.worker_start_ts:
            return (self.complete_ts - self.worker_start_ts) * 1000.0
        return 0.0

    @property
    def total_latency_ms(self) -> float:
        """Wall-clock time from enqueue to completion."""
        if self.complete_ts and self.enqueue_ts:
            return (self.complete_ts - self.enqueue_ts) * 1000.0
        return 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cost_per_token(self) -> float:
        """USD per token — 0.0 when token counts are unavailable."""
        return self.cost_usd / self.total_tokens if self.total_tokens > 0 else 0.0

    # ── Serialisation ─────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        d = asdict(self)
        d["queue_wait_ms"] = self.queue_wait_ms
        d["worker_latency_ms"] = self.worker_latency_ms
        d["total_latency_ms"] = self.total_latency_ms
        d["total_tokens"] = self.total_tokens
        d["cost_per_token"] = self.cost_per_token
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TaskSpan":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    # ── Human-readable summary ────────────────────────────────────────────

    def one_liner(self) -> str:
        status = "✓" if self.success else "✗"
        return (
            f"[OBS] {status} task={self.task_id} model={self.model_chosen} "
            f"total={self.total_latency_ms:.0f}ms "
            f"(queue={self.queue_wait_ms:.0f}ms worker={self.worker_latency_ms:.0f}ms) "
            f"cost=${self.cost_usd:.4f} attempts={self.attempt_count}"
        )


# ── MetricsSummary ────────────────────────────────────────────────────────────

@dataclass
class MetricsSummary:
    """Aggregate statistics computed from a sliding window of TaskSpans."""

    window_size: int
    total_tasks: int
    success_count: int
    failure_count: int

    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    avg_queue_wait_ms: float
    avg_worker_latency_ms: float

    throughput_per_min: float
    avg_cost_usd: float
    avg_cost_per_token: float
    total_cost_usd: float

    model_distribution: Dict[str, int]

    @property
    def success_rate(self) -> float:
        return self.success_count / self.total_tasks if self.total_tasks > 0 else 0.0

    def display(self) -> str:
        dist = "  ".join(
            f"{m}:{n}" for m, n in sorted(self.model_distribution.items())
        ) or "—"
        usd_per_mtoken = self.avg_cost_per_token * 1_000_000
        return "\n".join([
            f"┌─ METRICS (last {self.window_size} tasks) ──────────────────────────────",
            f"│  Tasks    {self.total_tasks}  success={self.success_rate:.0%}"
            f"  (✓{self.success_count} ✗{self.failure_count})",
            f"│  Latency  p50={self.p50_latency_ms:.0f}ms"
            f"  p95={self.p95_latency_ms:.0f}ms"
            f"  p99={self.p99_latency_ms:.0f}ms",
            f"│  Queue    avg={self.avg_queue_wait_ms:.1f}ms"
            f"  Worker avg={self.avg_worker_latency_ms:.0f}ms",
            f"│  Cost     avg=${self.avg_cost_usd:.4f}/task"
            f"  total=${self.total_cost_usd:.4f}"
            f"  ${usd_per_mtoken:.2f}/MTok",
            f"│  Thruput  {self.throughput_per_min:.2f} tasks/min",
            f"│  Models   {dist}",
            f"└────────────────────────────────────────────────────────────────",
        ])


# ── ObservabilityStore ────────────────────────────────────────────────────────

class ObservabilityStore:
    """
    Append-only JSONL store for TaskSpans.

    One JSON object per line — human-readable, grep-able, git-diffable.
    Default path: .awos/spans.jsonl
    """

    def __init__(self, persist_dir: str = ".awos") -> None:
        self._path = Path(persist_dir) / "spans.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    # ── Write ─────────────────────────────────────────────────────────────

    def record(self, span: TaskSpan) -> None:
        """Append one completed span to the JSONL file."""
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(span.to_dict()) + "\n")

    # ── Read ──────────────────────────────────────────────────────────────

    def get_recent(self, n: int = 100) -> List[TaskSpan]:
        """Return the n most-recent spans (oldest first)."""
        if not self._path.exists():
            return []
        lines = self._path.read_text(encoding="utf-8").splitlines()
        spans: List[TaskSpan] = []
        for line in lines[-n:]:
            line = line.strip()
            if not line:
                continue
            try:
                spans.append(TaskSpan.from_dict(json.loads(line)))
            except Exception:
                pass
        return spans

    # ── Aggregate ─────────────────────────────────────────────────────────

    def async_projection(self, n_workers: int = 4, window: int = 100):
        """
        Predict async execution gains for n_workers concurrent workers.
        Returns AsyncProjection or None if no spans available.
        """
        from .async_engine import project_async_gains
        return project_async_gains(self.get_recent(window), n_workers=n_workers)

    def inference_report(self, window: int = 100):
        """
        Compute prefill/decode inference visibility from the last *window* spans.
        Returns an InferenceReport or None if no spans have token data.
        """
        from .inference_profile import InferenceProfiler
        return InferenceProfiler.from_spans(self.get_recent(window))

    def queue_state(self, window: int = 100):
        """
        Compute M/M/1 queue statistics from the last *window* spans.
        Returns a QueueState or None if too few samples.
        Importing here avoids circular dependency at module load time.
        """
        from .queue_model import MM1Queue
        return MM1Queue.from_spans(self.get_recent(window))

    def metrics(self, window: int = 100) -> MetricsSummary:
        """Compute MetricsSummary over the last *window* spans."""
        spans = self.get_recent(window)

        _empty = MetricsSummary(
            window_size=window, total_tasks=0, success_count=0, failure_count=0,
            p50_latency_ms=0.0, p95_latency_ms=0.0, p99_latency_ms=0.0,
            avg_queue_wait_ms=0.0, avg_worker_latency_ms=0.0,
            throughput_per_min=0.0, avg_cost_usd=0.0, avg_cost_per_token=0.0,
            total_cost_usd=0.0, model_distribution={},
        )
        if not spans:
            return _empty

        latencies = [s.total_latency_ms for s in spans]
        queue_waits = [s.queue_wait_ms for s in spans]
        worker_lats = [s.worker_latency_ms for s in spans]
        costs = [s.cost_usd for s in spans]
        cpt = [s.cost_per_token for s in spans if s.total_tokens > 0]

        success_count = sum(1 for s in spans if s.success)

        # Throughput over the observation window
        if len(spans) >= 2:
            elapsed_min = (spans[-1].complete_ts - spans[0].enqueue_ts) / 60.0
            throughput = len(spans) / elapsed_min if elapsed_min > 0 else 0.0
        else:
            throughput = 0.0

        model_dist: Dict[str, int] = {}
        for s in spans:
            model_dist[s.model_chosen] = model_dist.get(s.model_chosen, 0) + 1

        def _avg(lst: List[float]) -> float:
            return sum(lst) / len(lst) if lst else 0.0

        return MetricsSummary(
            window_size=window,
            total_tasks=len(spans),
            success_count=success_count,
            failure_count=len(spans) - success_count,
            p50_latency_ms=_percentile(latencies, 50),
            p95_latency_ms=_percentile(latencies, 95),
            p99_latency_ms=_percentile(latencies, 99),
            avg_queue_wait_ms=_avg(queue_waits),
            avg_worker_latency_ms=_avg(worker_lats),
            throughput_per_min=throughput,
            avg_cost_usd=_avg(costs),
            avg_cost_per_token=_avg(cpt),
            total_cost_usd=sum(costs),
            model_distribution=model_dist,
        )


# ── Factory ───────────────────────────────────────────────────────────────────

def new_span(task_id: Any, goal: str, action: str, file: str) -> TaskSpan:
    """Create a TaskSpan stamped with the current arrival time."""
    return TaskSpan(
        span_id=uuid.uuid4().hex[:12],
        task_id=str(task_id),
        goal=goal,
        action=action,
        file=file,
        enqueue_ts=time.time(),
    )
