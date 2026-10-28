"""
ResponseCache: exact-request response cache with a sliding TTL.

A cached answer may only be returned for a request that would have produced
the same answer, so the key covers everything the provider sees:

  sha256 over (provider, wire model, model version / identity, template or
  grammar digest, the full rendered message list — system prompt, injected
  context and history included — and the sampling parameters).

Nothing is normalised: case and whitespace change what a model reads, so
"Explain X" and "explain  x" are different requests.

Only deterministic requests are cached: temperature must be given and be 0
(an unspecified temperature means the provider default, usually 1.0). For any
other request make_key returns None, and get(None) / set(None, ...) are a
miss / a no-op, so callers can pass the key straight through.

TTL is sliding: an entry expires `ttl` seconds after it was last written or
read; each hit refreshes read_at. `max_age_seconds` (optional) caps the age
since written_at regardless of reads. Every internal error is a miss.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class _Entry:
    response: str
    model: str
    written_at: float
    read_at: float
    hits: int = 0


def _digest(value: Any) -> str:
    if value is None or value == "":
        return ""
    if not isinstance(value, (str, bytes)):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def cacheable(sampling: Optional[dict]) -> bool:
    """True only for a request with an explicit temperature of 0."""
    try:
        temp = (sampling or {}).get("temperature")
        return temp is not None and not isinstance(temp, bool) and float(temp) <= 0.0
    except (TypeError, ValueError, AttributeError):
        return False


class ResponseCache:
    """Exact-request response cache (see module docstring)."""

    DEFAULT_TTL = 900   # seconds since last write/read
    MAX_ENTRIES = 500   # prevent unbounded growth

    def __init__(self, ttl_seconds: int = DEFAULT_TTL, max_age_seconds: Optional[float] = None,
                 clock=time.time):
        self.ttl = ttl_seconds
        self.max_age = max_age_seconds
        self._clock = clock
        self._cache: dict[str, _Entry] = {}
        self._hits = 0
        self._misses = 0
        self._errors = 0

    # ── Public API ────────────────────────────────────────────────────────────

    def make_key(self, model: str, user_input: Optional[str] = None, *, provider: str = "",
                 model_version: str = "", template: Any = None, messages: Optional[list] = None,
                 system: Optional[str] = None, sampling: Optional[dict] = None) -> Optional[str]:
        """sha256 key for one request, or None when it must not be cached.

        model     the wire model id actually sent
        provider  who serves it (openrouter / deepseek / anthropic / local ...)
        model_version  model identity beyond the id (snapshot, GGUF hash ...)
        template  chat template or grammar text (or its digest)
        messages  the full message list as sent; `system` and `user_input`
                  are added as the first / last message when given
        sampling  every sampling parameter sent (temperature, top_p, max_tokens ...)
        """
        try:
            if not cacheable(sampling):
                return None
            msgs = list(messages or [])
            if system is not None:
                msgs.insert(0, {"role": "system", "content": system})
            if user_input is not None:
                msgs.append({"role": "user", "content": user_input})
            payload = {
                "v": 2,
                "provider": provider or "",
                "model": model or "",
                "model_version": model_version or "",
                "template": _digest(template),
                "messages": msgs,
                "sampling": sampling,
            }
            raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
            return hashlib.sha256(raw.encode("utf-8")).hexdigest()
        except Exception:
            self._errors += 1
            return None

    def get(self, key: Optional[str]) -> Optional[str]:
        """Cached response if fresh (refreshing its sliding TTL), else None."""
        try:
            entry = self._cache.get(key) if key else None
            if entry is None:
                self._misses += 1
                return None
            now = self._clock()
            if self._expired(entry, now):
                self._cache.pop(key, None)
                self._misses += 1
                return None
            entry.read_at = now
            entry.hits += 1
            self._hits += 1
            return entry.response
        except Exception:
            self._errors += 1
            self._misses += 1
            return None

    def set(self, key: Optional[str], response: str, model: str = "unknown") -> None:
        """Store a response (no-op for a None key or a non-string response)."""
        try:
            if not key or not isinstance(response, str):
                return
            if key not in self._cache and len(self._cache) >= self.MAX_ENTRIES:
                self._evict_oldest(count=max(1, self.MAX_ENTRIES // 10))
            now = self._clock()
            self._cache[key] = _Entry(response, model, now, now)
        except Exception:
            self._errors += 1

    def invalidate(self, key: Optional[str]) -> None:
        self._cache.pop(key, None)

    def clear(self) -> None:
        """Wipe the cache (e.g. after codebase changes)."""
        self._cache.clear()
        self._hits = 0
        self._misses = 0

    def prune_expired(self) -> None:
        now = self._clock()
        for k in [k for k, e in self._cache.items() if self._expired(e, now)]:
            del self._cache[k]

    # ── Stats ─────────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        total = self._hits + self._misses
        hit_rate = self._hits / total if total > 0 else 0.0
        return {
            "entries": len(self._cache),
            "hits": self._hits,
            "misses": self._misses,
            "errors": self._errors,
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

    def _expired(self, entry: _Entry, now: float) -> bool:
        if now - max(entry.read_at, entry.written_at) > self.ttl:
            return True
        return self.max_age is not None and now - entry.written_at > self.max_age

    def _evict_oldest(self, count: int = 50) -> None:
        """Drop the `count` least recently used entries."""
        by_age = sorted(self._cache.items(), key=lambda kv: max(kv[1].read_at, kv[1].written_at))
        for k, _ in by_age[:count]:
            del self._cache[k]
