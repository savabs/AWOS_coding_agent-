"""
test_cache_telemetry.py — Tests for AWOS agent framework caching and telemetry mechanisms.

This module validates the caching and telemetry systems used by the AWOS agent
framework to track and report cache performance metrics. It focuses on how
cache interactions are logged and reported across different AI model providers.
The tests cover:

  • Extraction of cache statistics from Anthropic API responses
  • Extraction of cache statistics from OpenAI API responses
  • Recording cache events to persistent JSONL storage
  • Aggregation of cache statistics over time windows
  • Edge cases such as empty stores with no recorded events

The tests cover both the data extraction layer (parsing provider-specific
response formats) and the storage/aggregation layer (CacheTelemetryStore),
ensuring accurate tracking of cache hit rates, token savings, and cost
reductions from cached responses.
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Add agent dir to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "scaffold" / "agent"))

from cache_telemetry import (
    CacheEvent,
    CacheTelemetryStore,
    extract_cache_stats_anthropic,
    extract_cache_stats_openai,
)


def test_extract_anthropic_cache_stats():
    """Test extraction from Anthropic API response."""
    response = {
        "usage": {
            "input_tokens": 1250,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 1150,
            "output_tokens": 85,
        }
    }
    
    event = extract_cache_stats_anthropic(response, "worker", "claude-3-5-sonnet")
    
    assert event.input_tokens == 1250
    assert event.cache_read_tokens == 1150
    assert event.cache_creation_tokens == 0
    assert event.cache_hit_rate == pytest.approx(1150 / 1250, rel=0.01)
    assert event.cost_saved_usd > 0  # saved by using cache


def test_extract_openai_cache_stats():
    """Test extraction from OpenAI API response."""
    response = {
        "usage": {
            "prompt_tokens": 1000,
            "prompt_tokens_details": {
                "cached_tokens": 950,
            },
            "completion_tokens": 50,
        }
    }
    
    event = extract_cache_stats_openai(response, "planner", "gpt-4")
    
    assert event.input_tokens == 1000
    assert event.cache_read_tokens == 950
    assert event.cache_hit_rate == pytest.approx(0.95, rel=0.01)


def test_cache_telemetry_store_record(tmp_path):
    """Test recording events to jsonl."""
    store = CacheTelemetryStore(store_path=str(tmp_path))
    
    event = CacheEvent(
        timestamp=datetime.utcnow().isoformat(),
        component="worker",
        model="test-model",
        input_tokens=100,
        cache_read_tokens=90,
        cache_creation_tokens=0,
        cache_hit_rate=0.9,
        cost_saved_usd=0.01,
    )
    
    store.record(event)
    
    # Verify file created
    assert store.file.exists()
    
    # Verify content
    lines = store.file.read_text().strip().split("\n")
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["component"] == "worker"
    assert data["cache_hit_rate"] == 0.9


def test_cache_telemetry_store_stats(tmp_path):
    """Test aggregating stats."""
    store = CacheTelemetryStore(store_path=str(tmp_path))
    
    now = datetime.utcnow()
    
    # Record 3 events
    for i in range(3):
        event = CacheEvent(
            timestamp=(now - timedelta(hours=i)).isoformat(),
            component="worker",
            model="test",
            input_tokens=1000,
            cache_read_tokens=900,
            cache_creation_tokens=0,
            cache_hit_rate=0.9,
            cost_saved_usd=0.01,
        )
        store.record(event)
    
    # Get stats for last 24h
    stats = store.stats(since=now - timedelta(hours=24))
    
    assert stats["event_count"] == 3
    assert stats["tokens_cached"] == 2700  # 900 * 3
    assert stats["tokens_fresh"] == 300    # (1000 - 900) * 3
    assert stats["hit_rate"] == pytest.approx(0.9, rel=0.01)
    assert stats["cost_saved"] > 0


def test_cache_telemetry_store_no_file(tmp_path):
    """Test stats when no events recorded."""
    store = CacheTelemetryStore(store_path=str(tmp_path))
    
    stats = store.stats()
    
    assert stats["hit_rate"] == 0.0
    assert stats["event_count"] == 0
