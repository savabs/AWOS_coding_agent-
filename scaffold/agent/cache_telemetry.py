"""
cache_telemetry.py — Prompt cache hit rate tracking.

Measures cache efficiency to prove 95%+ hit rate on long missions.
Stores per-call cache stats to `.awos/cache_stats.jsonl`.

Spec: docs/specs/cache_telemetry_spec.md
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class CacheEvent:
    """Single cache stat observation from an API call."""
    
    timestamp: str
    component: str  # "worker", "planner", "critic", etc.
    model: str
    input_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    cache_hit_rate: float  # cache_read / input_tokens
    cost_saved_usd: float  # estimated savings from cache hit


class CacheTelemetryStore:
    """
    Persistent store for cache hit telemetry.
    
    Appends events to .awos/cache_stats.jsonl.
    Aggregates stats for reporting.
    """
    
    def __init__(self, store_path: str = ".awos"):
        self.store_path = Path(store_path)
        self.file = self.store_path / "cache_stats.jsonl"
        self.store_path.mkdir(parents=True, exist_ok=True)
    
    def record(self, event: CacheEvent) -> None:
        """Append cache event to jsonl."""
        try:
            with self.file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(event)) + "\n")
        except Exception as exc:
            logger.warning("[CacheTelemetry] failed to record: %s", exc)
    
    def stats(self, since: Optional[datetime] = None) -> dict:
        """
        Aggregate cache stats since a given time.
        
        Returns:
            {
                "hit_rate": float,       # overall cache hit %
                "tokens_cached": int,    # total cache_read_tokens
                "tokens_fresh": int,     # total non-cached input
                "cost_saved": float,     # total $ saved
                "event_count": int,
            }
        """
        if not self.file.exists():
            return {
                "hit_rate": 0.0,
                "tokens_cached": 0,
                "tokens_fresh": 0,
                "cost_saved": 0.0,
                "event_count": 0,
            }
        
        since_ts = since.isoformat() if since else "1970-01-01T00:00:00"
        
        total_input = 0
        total_cached = 0
        total_cost_saved = 0.0
        count = 0
        
        try:
            with self.file.open("r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    if event["timestamp"] < since_ts:
                        continue
                    
                    total_input += event["input_tokens"]
                    total_cached += event["cache_read_tokens"]
                    total_cost_saved += event["cost_saved_usd"]
                    count += 1
        except Exception as exc:
            logger.warning("[CacheTelemetry] failed to read stats: %s", exc)
        
        hit_rate = total_cached / total_input if total_input > 0 else 0.0
        tokens_fresh = total_input - total_cached
        
        return {
            "hit_rate": hit_rate,
            "tokens_cached": total_cached,
            "tokens_fresh": tokens_fresh,
            "cost_saved": total_cost_saved,
            "event_count": count,
        }


def extract_cache_stats_anthropic(
    response: dict,
    component: str,
    model: str,
    price_per_m_tokens: float = 3.0,  # Anthropic Claude 3.5 Sonnet input
) -> CacheEvent:
    """
    Extract cache stats from Anthropic API response.
    
    Args:
        response: API response dict with "usage" field
        component: "worker", "planner", "critic", etc.
        model: Model name
        price_per_m_tokens: Input token price per million (for cost calc)
    
    Returns:
        CacheEvent with extracted stats
    """
    usage = response.get("usage", {})
    input_tokens = usage.get("input_tokens", 0)
    cache_read = usage.get("cache_read_input_tokens", 0)
    cache_create = usage.get("cache_creation_input_tokens", 0)
    
    hit_rate = cache_read / input_tokens if input_tokens > 0 else 0.0
    
    # Anthropic pricing: cached tokens are 90% cheaper
    # (write tokens full price, read tokens 10% price)
    cost_saved = (cache_read * 0.9 * price_per_m_tokens) / 1_000_000
    
    return CacheEvent(
        timestamp=datetime.utcnow().isoformat(),
        component=component,
        model=model,
        input_tokens=input_tokens,
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_create,
        cache_hit_rate=hit_rate,
        cost_saved_usd=cost_saved,
    )


def extract_cache_stats_openai(
    response: dict,
    component: str,
    model: str,
    price_per_m_tokens: float = 2.5,  # GPT-4 input
) -> CacheEvent:
    """
    Extract cache stats from OpenAI API response.
    
    OpenAI uses `prompt_tokens_details.cached_tokens` field.
    """
    usage = response.get("usage", {})
    input_tokens = usage.get("prompt_tokens", 0)
    cached = usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
    
    hit_rate = cached / input_tokens if input_tokens > 0 else 0.0
    cost_saved = (cached * 0.5 * price_per_m_tokens) / 1_000_000  # 50% discount
    
    return CacheEvent(
        timestamp=datetime.utcnow().isoformat(),
        component=component,
        model=model,
        input_tokens=input_tokens,
        cache_read_tokens=cached,
        cache_creation_tokens=0,  # OpenAI doesn't separate create vs read
        cache_hit_rate=hit_rate,
        cost_saved_usd=cost_saved,
    )
