"""
ResponseCache: TTL-based deduplication cache for AWOS agent responses.

Why this matters:
  - Repeated questions ("explain this function", "what does X do") hit the same model call.
  - With a 15-minute cache, identical or near-identical inputs return instantly at $0.
  - For a 1,500 req/month workload, even 10% cache hits = 150 free responses = ~$1.50 saved.

Architecture:
  - Key = MD5(model + normalized_input)
  - Value = (response_text, timestamp, model_used)
  - TTL = configurable (default 15 min for coding, 5 min for reasoning)
  - Thread-safe via dict writes (GIL protected for CPython)
"""

import hashlib
import time
import re
from typing import Optional


class ResponseCache:
    """TTL-based response deduplication cache."""

    DEFAULT_TTL = 900   # 15 minutes — good for coding tasks (same question = same answer)
    MAX_ENTRIES = 500   # Prevent unbounded growth

    def __init__(self, ttl_seconds: int = DEFAULT_TTL):
        self.ttl = ttl_seconds
        self._cache: dict[str, tuple] = {}   # key → (response, timestamp, model, hit_count)
        self._hits = 0
        self._misses = 0

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, key: str) -> Optional[str]:
        """Return cached response if fresh, else None."""
        if key not in self._cache:
            self._misses += 1
            return None

        response, ts, model, hit_count = self._cache[key]
        if time.time() - ts > self.ttl:
            del self._cache[key]
            self._misses += 1
            return None

        # Update hit count
        self._cache[key] = (response, ts, model, hit_count + 1)
        self._hits += 1
        return response

    def set(self, key: str, response: str, model: str = "unknown"):
        """Store a response. Evicts oldest entries if over MAX_ENTRIES."""
        if len(self._cache) >= self.MAX_ENTRIES:
            self._evict_oldest(count=50)
        self._cache[key] = (response, time.time(), model, 0)

    def make_key(self, model: str, user_input: str) -> str:
        """Create a cache key from model + normalized input."""
        normalized = _normalize(user_input)
        raw = f"{model}::{normalized}"
        return hashlib.md5(raw.encode()).hexdigest()

    def invalidate(self, key: str):
        """Explicitly remove a cached entry."""
        self._cache.pop(key, None)

    def clear(self):
        """Wipe the cache (e.g. after codebase changes)."""
        self._cache.clear()
        self._hits = 0
        self._misses = 0

    def prune_expired(self):
        """Remove all expired entries."""
        now = time.time()
        expired = [k for k, (_, ts, _, _) in self._cache.items() if now - ts > self.ttl]
        for k in expired:
            del self._cache[k]

    # ── Stats ─────────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        total = self._hits + self._misses
        hit_rate = self._hits / total if total > 0 else 0.0
        return {
            "entries": len(self._cache),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": f"{hit_rate:.1%}",
            "estimated_savings_requests": self._hits,
        }

    def summary(self) -> str:
        s = self.stats()
        return (
            f"ResponseCache: {s['entries']} entries | "
            f"{s['hit_rate']} hit rate ({s['hits']} hits / {s['misses']} misses)"
        )

    def hit_rate_float(self) -> float:
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    # ── Private ───────────────────────────────────────────────────────────────

    def _evict_oldest(self, count: int = 50):
        """Remove the oldest `count` entries."""
        by_age = sorted(self._cache.items(), key=lambda x: x[1][1])
        for k, _ in by_age[:count]:
            del self._cache[k]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Normalize text for cache key — collapse whitespace, lowercase."""
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text
