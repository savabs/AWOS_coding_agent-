"""
Phase 3: Inference Visibility — Prefill/Decode Decomposition

Modern LLM inference has two distinct phases with very different computational
characteristics. Understanding them explains cost, latency, and batching behaviour.

┌─ PREFILL PHASE ─────────────────────────────────────────────────────────────┐
│ All input tokens are processed in parallel.                                  │
│ COMPUTE-BOUND: work ∝ input_tokens² (attention) + input_tokens (FFN).       │
│ GPU arithmetic units are the bottleneck. Fast on big batches.                │
│ KV cache is WRITTEN here (K, V tensors for every layer and head).            │
│ Time-to-first-token (TTFT) ≈ proportional to input_tokens.                  │
└──────────────────────────────────────────────────────────────────────────────┘
┌─ DECODE PHASE ──────────────────────────────────────────────────────────────┐
│ Output tokens generated ONE AT A TIME (auto-regressive).                     │
│ MEMORY-BANDWIDTH-BOUND: each step reads the full KV cache.                  │
│ KV cache is READ each step — larger context = slower decode.                 │
│ Time-per-output-token (TPOT) ≈ roughly constant per model (BW-limited).     │
│ Total decode time = TPOT × output_tokens.                                    │
└──────────────────────────────────────────────────────────────────────────────┘

LATENCY DECOMPOSITION (estimated from observable data):
    total_latency ≈ TTFT + TPOT × output_tokens
    TTFT          ≈ worker_latency × (input_tokens / total_tokens)
    TPOT          ≈ (worker_latency - TTFT) / output_tokens

PROMPT CACHING:
    If the same system-prompt / codebase context is reused across retries,
    the KV cache from prefill can be REUSED — effectively paying only once.
    Anthropic prompt caching: 90% cost reduction on cached input tokens.
    For AWOS (typically prefill-heavy, >70% input ratio), this is the single
    highest-leverage cost optimisation available.

CONTINUOUS BATCHING (PagedAttention / vLLM):
    Static batching: all requests in a batch run until the LONGEST finishes.
    Wasted GPU time = (max_length - actual_length) × avg_tpot per request.
    Continuous batching: as one request finishes, a new one joins immediately.
    Throughput improvement = N× for N concurrent requests until KV cache full.
    Throughput model (simplified): T(B) = B / (1 + β·log₂(B))
    where β≈0.15 accounts for KV cache memory pressure adding latency.

References:
    Dao, T. et al. (2022). FlashAttention: Fast and Memory-Efficient Exact Attention.
    Kwon, W. et al. (2023). Efficient Memory Management for LLMs with PagedAttention.
    Pope, R. et al. (2023). Efficiently Scaling Transformer Inference. MLSys.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .observability import TaskSpan


# ── Constants ─────────────────────────────────────────────────────────────────

# Anthropic prompt caching: cached tokens billed at 10% of normal input price.
CACHE_SAVINGS_RATIO = 0.90

# Latency overhead factor per doubling of batch size (continuous batching model).
# Derivation: each 2× batch adds ~15% latency due to KV cache memory pressure.
# Empirical from Kwon et al. 2023 (vLLM) throughput curves.
BATCH_LATENCY_OVERHEAD = 0.15

# Workload classification thresholds
PREFILL_HEAVY_THRESHOLD = 0.65   # prefill_ratio > 0.65 → prompt-heavy workload
DECODE_HEAVY_THRESHOLD  = 0.35   # prefill_ratio < 0.35 → generation-heavy workload

# Minimum tokens required for meaningful per-task decomposition
MIN_TOKENS = 10


# ── Per-task token profile ────────────────────────────────────────────────────

@dataclass
class TokenProfile:
    """
    Inference decomposition for a single task.

    All time estimates are rough — we cannot directly observe TTFT/TPOT from
    API calls. The estimates assume latency splits proportionally with token
    counts, which is approximately correct for standard transformer architectures.
    """

    input_tokens: int
    output_tokens: int
    total_tokens: int

    # Ratios
    prefill_ratio: float          # input / total — fraction of work that's prefill
    decode_ratio: float           # output / total
    token_efficiency: float       # output / input — generation per prompt token

    # Latency decomposition (ms) — estimated, not measured
    worker_latency_ms: float
    estimated_ttft_ms: float      # ≈ worker_latency × prefill_ratio
    estimated_tpot_ms: float      # ≈ (latency - ttft) / output_tokens

    # Throughput estimates
    decode_throughput_tps: float  # output tokens / decode_time (tokens/sec)
    prefill_throughput_tps: float # input tokens / ttft (tokens/sec)

    # Prompt caching potential
    cache_savings_usd: float      # 90% × prefill_fraction × cost_usd
    model: str = "unknown"

    @property
    def is_prefill_heavy(self) -> bool:
        return self.prefill_ratio >= PREFILL_HEAVY_THRESHOLD

    @property
    def is_decode_heavy(self) -> bool:
        return self.prefill_ratio <= DECODE_HEAVY_THRESHOLD

    @property
    def workload_label(self) -> str:
        if self.is_prefill_heavy:
            return "prefill_heavy"
        if self.is_decode_heavy:
            return "decode_heavy"
        return "balanced"


# ── Aggregate inference report ────────────────────────────────────────────────

@dataclass
class InferenceReport:
    """
    Aggregated inference visibility across a window of recent task spans.

    Answers:
      - Is our workload prefill-heavy or decode-heavy?
      - What is our average TTFT / TPOT?
      - How much could prompt caching save?
      - What throughput gain would batching provide?
    """

    n_samples: int

    # Token distribution
    avg_input_tokens: float
    avg_output_tokens: float
    avg_total_tokens: float
    avg_prefill_ratio: float
    avg_token_efficiency: float   # output_tokens / input_tokens

    # Latency decomposition (ms)
    avg_ttft_ms: float
    avg_tpot_ms: float            # ms per output token
    avg_decode_throughput_tps: float
    avg_prefill_throughput_tps: float

    # Prompt caching potential
    total_cache_savings_usd: float
    total_actual_cost_usd: float
    cache_savings_pct: float      # % of total spend recoverably via caching

    # Workload classification
    workload_type: str            # "prefill_heavy" | "decode_heavy" | "balanced"

    # Theoretical batching gain (throughput multiplier vs single-task sequential)
    # T(B) = B / (1 + β·log₂(B)), β=0.15
    batch_gain_x2: float
    batch_gain_x4: float
    batch_gain_x8: float

    # Per-model breakdown
    model_prefill_ratios: Dict[str, float]   # model → avg prefill ratio

    def display(self) -> str:
        workload_icon = {
            "prefill_heavy": "📥",
            "decode_heavy": "📤",
            "balanced": "⚖",
        }.get(self.workload_type, "?")

        cache_line = (
            f"│  Cache savings  ${self.total_cache_savings_usd:.4f} potential "
            f"({self.cache_savings_pct:.1f}% of spend) via prompt caching"
        )
        batch_line = (
            f"│  Batching gain  2×={self.batch_gain_x2:.1f}×  "
            f"4×={self.batch_gain_x4:.1f}×  "
            f"8×={self.batch_gain_x8:.1f}× throughput"
        )
        model_lines = "  ".join(
            f"{m}:{r:.2f}"
            for m, r in sorted(self.model_prefill_ratios.items())
        ) or "—"

        return "\n".join([
            f"┌─ INFERENCE PROFILE  (n={self.n_samples}) ──────────────────────────────",
            f"│  Workload  {workload_icon} {self.workload_type}  "
            f"avg_input={self.avg_input_tokens:.0f}tok  "
            f"avg_output={self.avg_output_tokens:.0f}tok  "
            f"efficiency={self.avg_token_efficiency:.3f}",
            f"│  Prefill   ratio={self.avg_prefill_ratio:.2f}  "
            f"TTFT≈{self.avg_ttft_ms:.0f}ms  "
            f"throughput≈{self.avg_prefill_throughput_tps:.0f}tok/s",
            f"│  Decode    TPOT≈{self.avg_tpot_ms:.1f}ms/tok  "
            f"throughput≈{self.avg_decode_throughput_tps:.0f}tok/s",
            cache_line,
            batch_line,
            f"│  Per-model prefill ratios  {model_lines}",
            f"└────────────────────────────────────────────────────────────────",
        ])


# ── InferenceProfiler ─────────────────────────────────────────────────────────

class InferenceProfiler:
    """
    Computes inference visibility metrics from TaskSpans.

    No external dependencies — pure arithmetic on token counts and latencies.
    """

    @staticmethod
    def batch_throughput_factor(batch_size: int) -> float:
        """
        Estimated throughput multiplier for batch_size concurrent requests
        using continuous batching (PagedAttention model).

        Model: T(B) = B / (1 + β·log₂(B))
        where β = BATCH_LATENCY_OVERHEAD = 0.15

        Derivation:
            - Each request still takes latency(1) as base.
            - Doubling B adds β fraction of extra latency (memory pressure).
            - Throughput = concurrent requests / per-request latency
            - = B / (1 + β·log₂(B))

        Note: This models the memory-bandwidth bottleneck during decode.
        Prefill batches better (compute-bound), but decode is the limiting phase
        for most generative workloads.
        """
        if batch_size <= 1:
            return 1.0
        return batch_size / (1.0 + BATCH_LATENCY_OVERHEAD * math.log2(batch_size))

    @staticmethod
    def per_task(span: "TaskSpan", cost_usd: float = 0.0) -> Optional[TokenProfile]:
        """
        Decompose a single span into prefill/decode metrics.
        Returns None if token counts are too small for meaningful analysis.
        """
        inp = span.input_tokens
        out = span.output_tokens
        total = inp + out

        if total < MIN_TOKENS or span.worker_latency_ms <= 0:
            return None

        prefill_ratio = inp / total
        decode_ratio = out / total
        token_eff = out / inp if inp > 0 else 0.0

        lat_ms = span.worker_latency_ms
        ttft_ms = lat_ms * prefill_ratio
        decode_ms = lat_ms * decode_ratio

        # TPOT: guard against zero output tokens
        tpot_ms = decode_ms / out if out > 0 else 0.0

        # Throughput (tokens/sec)
        prefill_tps = (inp / (ttft_ms / 1000.0)) if ttft_ms > 0 else 0.0
        decode_tps = (out / (decode_ms / 1000.0)) if decode_ms > 0 and out > 0 else 0.0

        # Prompt cache savings: 90% of the prefill portion of cost
        actual_cost = cost_usd if cost_usd > 0 else span.cost_usd
        cache_savings = actual_cost * prefill_ratio * CACHE_SAVINGS_RATIO

        return TokenProfile(
            input_tokens=inp,
            output_tokens=out,
            total_tokens=total,
            prefill_ratio=prefill_ratio,
            decode_ratio=decode_ratio,
            token_efficiency=token_eff,
            worker_latency_ms=lat_ms,
            estimated_ttft_ms=ttft_ms,
            estimated_tpot_ms=tpot_ms,
            decode_throughput_tps=decode_tps,
            prefill_throughput_tps=prefill_tps,
            cache_savings_usd=cache_savings,
            model=span.model_chosen,
        )

    @classmethod
    def from_spans(cls, spans: List["TaskSpan"]) -> Optional[InferenceReport]:
        """
        Aggregate inference metrics from a list of TaskSpans.
        Returns None if no spans have meaningful token data.
        """
        profiles = [cls.per_task(s) for s in spans]
        profiles = [p for p in profiles if p is not None]

        if not profiles:
            return None

        n = len(profiles)
        avg = lambda vals: sum(vals) / n

        avg_inp = avg([p.input_tokens for p in profiles])
        avg_out = avg([p.output_tokens for p in profiles])
        avg_total = avg([p.total_tokens for p in profiles])
        avg_pf = avg([p.prefill_ratio for p in profiles])
        avg_eff = avg([p.token_efficiency for p in profiles])
        avg_ttft = avg([p.estimated_ttft_ms for p in profiles])
        avg_tpot = avg([p.estimated_tpot_ms for p in profiles])
        avg_dtps = avg([p.decode_throughput_tps for p in profiles])
        avg_ptps = avg([p.prefill_throughput_tps for p in profiles])

        total_cache = sum(p.cache_savings_usd for p in profiles)
        total_cost = sum(s.cost_usd for s in spans if s.cost_usd > 0)

        cache_pct = (total_cache / total_cost * 100.0) if total_cost > 0 else 0.0

        # Workload classification
        if avg_pf >= PREFILL_HEAVY_THRESHOLD:
            wtype = "prefill_heavy"
        elif avg_pf <= DECODE_HEAVY_THRESHOLD:
            wtype = "decode_heavy"
        else:
            wtype = "balanced"

        # Per-model prefill ratio
        from collections import defaultdict
        model_pf_sums: Dict[str, float] = defaultdict(float)
        model_pf_counts: Dict[str, int] = defaultdict(int)
        for p in profiles:
            model_pf_sums[p.model] += p.prefill_ratio
            model_pf_counts[p.model] += 1
        model_pf = {
            m: model_pf_sums[m] / model_pf_counts[m]
            for m in model_pf_sums
        }

        return InferenceReport(
            n_samples=n,
            avg_input_tokens=avg_inp,
            avg_output_tokens=avg_out,
            avg_total_tokens=avg_total,
            avg_prefill_ratio=avg_pf,
            avg_token_efficiency=avg_eff,
            avg_ttft_ms=avg_ttft,
            avg_tpot_ms=avg_tpot,
            avg_decode_throughput_tps=avg_dtps,
            avg_prefill_throughput_tps=avg_ptps,
            total_cache_savings_usd=total_cache,
            total_actual_cost_usd=total_cost,
            cache_savings_pct=cache_pct,
            workload_type=wtype,
            batch_gain_x2=cls.batch_throughput_factor(2),
            batch_gain_x4=cls.batch_throughput_factor(4),
            batch_gain_x8=cls.batch_throughput_factor(8),
            model_prefill_ratios=model_pf,
        )
