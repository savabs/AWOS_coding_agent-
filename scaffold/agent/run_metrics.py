"""
run_metrics.py — Per-run efficiency metrics (speed, tokens, cost, quality).

Design principle (from usage_record.py):
  Tokens are the PRIMARY measure (from API responses).
  Cost is DERIVED (tokens × model price table).
  Never trust a single "total tokens" number without in/out split.

Why tokens are tricky:
  - Input is mostly fixed "context tax" (docs, SOUL, history) — not user work.
  - Output is actual generation — scales with answer length.
  - Same total tokens on Haiku vs Opus = wildly different $.
  - Cache hits = 0 API tokens but real savings.
  - Retries multiply tokens without user-visible value.

Normalized metrics (comparable across runs):
  - tokens_per_success: total_tokens / (1 if success else 0.5 penalty)
  - cost_per_success_usd: cost / success weight
  - gen_tokens_per_sec: output_tokens / latency_sec (effective throughput)
  - context_ratio: context_tokens / input_tokens (lower = leaner prompts)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from scaffold.agent.usage_record import cost_from_tokens


# $/MTok — keep in sync with escalation_engine / worker fallbacks
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "deepseek-chat": (0.14, 0.28),
    "deepseek": (0.14, 0.28),
    "claude-haiku-4-5": (0.80, 4.00),
    "claude-haiku": (0.80, 4.00),
    "claude-sonnet": (5.0, 15.0),
    "cache": (0.0, 0.0),
}


def estimate_tokens(text: str) -> int:
    return max(0, len(text) // 4) if text else 0


def price_for_model(model: str) -> tuple[float, float]:
    key = (model or "").lower()
    for name, prices in MODEL_PRICES.items():
        if name in key:
            return prices
    return (0.14, 0.28)  # default cheap tier


@dataclass
class SpeedMetrics:
  ttfb_ms: float = 0.0          # time to first token (perceived speed)
  total_ms: float = 0.0         # wall clock end-to-end
  gen_tokens_per_sec: float = 0.0

  def to_dict(self) -> dict[str, float]:
    return {
      "ttfb_ms": round(self.ttfb_ms, 1),
      "total_ms": round(self.total_ms, 1),
      "latency_sec": round(self.total_ms / 1000.0, 3),
      "gen_tokens_per_sec": round(self.gen_tokens_per_sec, 1),
    }


@dataclass
class TokenMetrics:
  prompt_tokens: int = 0        # API input (all context sent to model)
  completion_tokens: int = 0    # API output (generation)
  context_tokens_est: int = 0     # estimated: docs + SOUL + history (not user query)
  query_tokens_est: int = 0       # estimated: user message portion of input
  total_tokens: int = 0
  cache_hit: bool = False
  tokens_saved_est: int = 0       # on cache hit: what we would have spent

  def to_dict(self) -> dict[str, int | bool]:
    return {
      "prompt_tokens": self.prompt_tokens,
      "completion_tokens": self.completion_tokens,
      "context_tokens_est": self.context_tokens_est,
      "query_tokens_est": self.query_tokens_est,
      "total_tokens": self.total_tokens,
      "cache_hit": self.cache_hit,
      "tokens_saved_est": self.tokens_saved_est,
      "context_ratio": round(
        self.context_tokens_est / self.prompt_tokens, 3
      ) if self.prompt_tokens else 0.0,
    }


@dataclass
class CostMetrics:
  cost_usd: float = 0.0
  cost_per_million_tokens: float = 0.0   # blended rate achieved
  baseline_sonnet_usd: float = 0.05    # naive "always sonnet" per comparable task
  savings_vs_sonnet_pct: float = 0.0

  def to_dict(self) -> dict[str, float]:
    return {
      "cost_usd": round(self.cost_usd, 6),
      "cost_per_million_tokens": round(self.cost_per_million_tokens, 4),
      "baseline_sonnet_usd": round(self.baseline_sonnet_usd, 4),
      "savings_vs_sonnet_pct": round(self.savings_vs_sonnet_pct, 1),
    }


@dataclass
class QualityMetrics:
  success: bool = True
  handler: str = "qa"
  cache_hit: bool = False

  def to_dict(self) -> dict[str, Any]:
    return {"success": self.success, "handler": self.handler, "cache_hit": self.cache_hit}


@dataclass
class RunMetrics:
  """One user-visible run (chat message, plan, or agent goal)."""
  model: str = "unknown"
  mode: str = "qa"
  speed: SpeedMetrics = field(default_factory=SpeedMetrics)
  tokens: TokenMetrics = field(default_factory=TokenMetrics)
  cost: CostMetrics = field(default_factory=CostMetrics)
  quality: QualityMetrics = field(default_factory=QualityMetrics)
  pei_micro: float = 0.0  # (quality × speed) / tokens — same formula as PEIReport

  def to_dict(self) -> dict[str, Any]:
    return {
      "model": self.model,
      "mode": self.mode,
      "speed": self.speed.to_dict(),
      "tokens": self.tokens.to_dict(),
      "cost": self.cost.to_dict(),
      "quality": self.quality.to_dict(),
      "pei_micro": round(self.pei_micro, 2),
    }

  def to_meta(self) -> dict[str, Any]:
    """Flat dict for ChatMessage.meta / GUI display."""
    d = self.to_dict()
    flat = {
      "model": self.model,
      "handler": self.quality.handler,
      "success": self.quality.success,
      "cache_hit": self.tokens.cache_hit,
      "prompt_tokens": self.tokens.prompt_tokens,
      "completion_tokens": self.tokens.completion_tokens,
      "context_tokens_est": self.tokens.context_tokens_est,
      "query_tokens_est": self.tokens.query_tokens_est,
      "total_tokens": self.tokens.total_tokens,
      "tokens_saved_est": self.tokens.tokens_saved_est,
      "cost_usd": self.cost.cost_usd,
      "ttfb_ms": self.speed.ttfb_ms,
      "latency_sec": self.speed.total_ms / 1000.0 if self.speed.total_ms else 0.0,
      "gen_tokens_per_sec": self.speed.gen_tokens_per_sec,
      "savings_vs_sonnet_pct": self.cost.savings_vs_sonnet_pct,
      "pei_micro": self.pei_micro,
      "metrics": d,
    }
    return flat


def build_run_metrics(
  *,
  mode: str,
  model: str,
  prompt_tokens: int,
  completion_tokens: int,
  cost_usd: Optional[float] = None,
  context_text: str = "",
  query_text: str = "",
  ttfb_ms: float = 0.0,
  total_ms: float = 0.0,
  success: bool = True,
  cache_hit: bool = False,
  tokens_saved_est: int = 0,
  handler: str = "qa",
) -> RunMetrics:
  """Assemble a RunMetrics from raw observations."""
  inp_price, out_price = price_for_model(model)
  if cost_usd is None:
    cost_usd = cost_from_tokens(prompt_tokens, completion_tokens, inp_price, out_price)

  ctx_est = estimate_tokens(context_text)
  q_est = estimate_tokens(query_text)
  total = prompt_tokens + completion_tokens

  latency_sec = total_ms / 1000.0 if total_ms > 0 else 0.0
  gen_tps = completion_tokens / latency_sec if latency_sec > 0.01 and completion_tokens else 0.0

  baseline = cost_from_tokens(prompt_tokens, completion_tokens, 5.0, 15.0)  # sonnet equiv
  savings = ((baseline - cost_usd) / baseline * 100) if baseline > 0 else 0.0

  quality_score = 1.0 if success else 0.01
  speed_factor = min(10.0, 1.0 / max(latency_sec, 0.1)) if latency_sec else 1.0
  pei = (quality_score * speed_factor * 1000.0) / max(total, 1)

  blended_cpm = (cost_usd / total * 1_000_000) if total > 0 else 0.0

  return RunMetrics(
    model=model,
    mode=mode,
    speed=SpeedMetrics(ttfb_ms=ttfb_ms, total_ms=total_ms, gen_tokens_per_sec=gen_tps),
    tokens=TokenMetrics(
      prompt_tokens=prompt_tokens,
      completion_tokens=completion_tokens,
      context_tokens_est=ctx_est,
      query_tokens_est=q_est,
      total_tokens=total,
      cache_hit=cache_hit,
      tokens_saved_est=tokens_saved_est,
    ),
    cost=CostMetrics(
      cost_usd=cost_usd,
      cost_per_million_tokens=blended_cpm,
      baseline_sonnet_usd=baseline,
      savings_vs_sonnet_pct=savings,
    ),
    quality=QualityMetrics(success=success, handler=handler, cache_hit=cache_hit),
    pei_micro=pei,
  )
