"""
Uniform per-request pricing for the Harbor head-to-head (ci/HARBOR.md).

Every agent's cost is recomputed from raw token usage with ONE price table, so
agents that report no cost, or price with a stale LiteLLM table, are compared
on the same basis. Stdlib only: imported by harbor_agents/awos_agent.py (on the
Harbor host) and scripts/harbor_collect.py.

Claude Haiku 5.5 (claude-haiku-5-5), USD per million tokens, from the
claude-api skill's model notes (cached 2026-10-06; re-check the Anthropic
pricing page before quoting money):
  prompt <= 100K tokens: input 0.10, output 0.50
  prompt  > 100K tokens: input 0.50, output 2.50   (the "x5" long-prompt card)
  cache reads 0.1x the input rate, 5-minute cache writes 1.25x, 1-hour 2x.

ASSUMPTIONS (flagged in ci/HARBOR.md):
  * the 0.1x / 1.25x / 2x cache multipliers apply to whichever card the request
    is on (so a long-prompt cache read is 0.05/M) — the docs state them
    relative to "the input rate" without spelling out the long card;
  * "prompt" for the 100K split is the whole input: uncached + cache read +
    cache write tokens of that one request;
  * cache writes with no TTL breakdown are priced as 5-minute writes.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

MTOK = 1_000_000
LONG_PROMPT_THRESHOLD = 100_000
CACHE_READ_MULT = 0.1
CACHE_WRITE_5M_MULT = 1.25
CACHE_WRITE_1H_MULT = 2.0

#: model -> (short card (in, out), long card (in, out)) in USD per MTok
PRICES = {
    "claude-haiku-5-5": ((0.10, 0.50), (0.50, 2.50)),
}


def normalize_model(model: Optional[str]) -> str:
    """'anthropic/claude-haiku-5-5' -> 'claude-haiku-5-5'."""
    m = (model or "").strip().lower()
    return m.split("/", 1)[1] if "/" in m else m


def request_cost(model: str, prompt_tokens: int, output_tokens: int, cache_read: int = 0,
                 cache_write_5m: int = 0, cache_write_1h: int = 0) -> Optional[float]:
    """USD for ONE request. prompt_tokens is the whole prompt (cache reads and
    writes included). None for an unpriced model."""
    cards = PRICES.get(normalize_model(model))
    if cards is None:
        return None
    prompt = max(0, int(prompt_tokens or 0))
    read = min(max(0, int(cache_read or 0)), prompt)
    w5 = min(max(0, int(cache_write_5m or 0)), prompt - read)
    w1 = min(max(0, int(cache_write_1h or 0)), prompt - read - w5)
    fresh = prompt - read - w5 - w1
    price_in, price_out = cards[1] if prompt > LONG_PROMPT_THRESHOLD else cards[0]
    usd = (fresh * price_in + read * price_in * CACHE_READ_MULT
           + w5 * price_in * CACHE_WRITE_5M_MULT + w1 * price_in * CACHE_WRITE_1H_MULT
           + max(0, int(output_tokens or 0)) * price_out)
    return usd / MTOK


def total_cost(model: str, requests: Iterable[dict[str, Any]]) -> Optional[float]:
    """Sum of request_cost over dicts with keys prompt, output, cache_read,
    cache_write_5m, cache_write_1h. None when the model is unpriced."""
    total = 0.0
    for r in requests:
        c = request_cost(model, r.get("prompt", 0), r.get("output", 0),
                         r.get("cache_read", 0), r.get("cache_write_5m", 0),
                         r.get("cache_write_1h", 0))
        if c is None:
            return None
        total += c
    return total
