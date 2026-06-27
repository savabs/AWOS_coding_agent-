# Research — Cache Hit Telemetry

**Date:** 2026-06-24  
**Context:** Prove 95%+ cache hit rate claim; measure cost-per-task advantage

---

## Problem

AWOS uses cache-first architecture (fixed prefix, volatile content at end), but we don't measure cache hit rates yet.

**Need to prove:**
- 95–98% cache hit rate (industry best practice for coding agents)
- Cache-first design saves money vs naive prompting
- Context compaction maintains cache hits across long missions

**User requirement:**
> "Major API providers discount prompt cache hits by up to 90%, and coding agents typically sustain 95% to 98% cache hit rates if the engineering framework is designed properly."

We need telemetry to prove this.

---

## What to measure

| Metric | Where | Why |
|--------|-------|-----|
| Cache hit % per API call | Every LLM call | Core proof |
| Cached tokens vs total | Per call | Cost savings calculation |
| Cache hit rate by component | Worker, Planner, Critic | Find optimization opportunities |
| Cache hit rate over time | Long mission | Prove compaction works |

---

## API provider support

| Provider | Cache support | Response field |
|----------|---------------|----------------|
| Anthropic (Claude) | ✅ Prompt caching | `usage.cache_creation_input_tokens`, `usage.cache_read_input_tokens` |
| OpenAI (GPT) | ✅ Via `cached_tokens` | `usage.prompt_tokens_details.cached_tokens` |
| DeepSeek | ✅ (OpenAI-compatible) | Same as OpenAI |
| Google (Gemini) | ✅ Context caching | `usageMetadata.cachedContentTokenCount` |

**Note:** Anthropic is primary model for AWOS worker/planner.

---

## Implementation approach

### 1. Capture cache stats from API response

Every LLM call returns usage metadata. Extract:
- `cache_read_input_tokens` (Anthropic) or `cached_tokens` (OpenAI)
- `input_tokens` (total)
- Calculate: `cache_hit_rate = cache_read / input_tokens`

### 2. Store in telemetry

Add to existing stores:
- `ObservabilityStore` (spans) — per-task cache stats
- `RewardStore` — cache hit % as feature for learning
- New: `CacheTelemetryStore` — `.awos/cache_stats.jsonl`

### 3. Report in CLI

Update `awos stats` to show:
```
Cache Performance (last 7 days):
  Hit rate: 96.3%
  Cached: 1.2M tokens
  Fresh: 47K tokens
  Savings: $12.34 (90% discount on cached)
```

---

## Files to touch

| File | Change |
|------|--------|
| `scaffold/agent/worker.py` | Extract cache stats from response, log to telemetry |
| `scaffold/agent/planner.py` | Same |
| `scaffold/agent/critic_engine.py` | Same |
| `scaffold/agent/cache_telemetry.py` | New store for cache stats |
| `scaffold/agent/self_learning_metrics.py` | Add cache stats to report |
| `awos.py` | Update `cmd_stats` to show cache performance |

---

## Example API response (Anthropic)

```json
{
  "usage": {
    "input_tokens": 1250,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 1150,
    "output_tokens": 85
  }
}
```

**Cache hit rate:** 1150 / 1250 = **92%**

If cache creation happened earlier:
```json
{
  "usage": {
    "input_tokens": 1250,
    "cache_creation_input_tokens": 1150,
    "cache_read_input_tokens": 0,
    "output_tokens": 85
  }
}
```

Next call reuses the cache:
```json
{
  "usage": {
    "input_tokens": 1250,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 1150,
    "output_tokens": 90
  }
}
```

---

## Success criteria

After implementation + long mission:
- [ ] Cache hit rate visible in `awos stats`
- [ ] Cache hit rate ≥ 95% on multi-task mission
- [ ] Cache stats stored in `.awos/cache_stats.jsonl`
- [ ] Cost savings calculation (cached vs fresh token pricing)

---

## Related

- Anthropic prompt caching: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
- OpenAI prompt caching: https://platform.openai.com/docs/guides/prompt-caching
- AWOS cache-first architecture: worker.py, context compaction in session_memory.py
