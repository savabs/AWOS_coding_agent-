# Spec — Cache Hit Telemetry

**Research:** `docs/research/cache_telemetry.md`  
**Task:** `tasks/active/cache_telemetry.md`

---

## Goal

Measure and report cache hit rates to prove 95%+ cache efficiency claim.

---

## Scope

### Phase 1 (this PR)
1. Extract cache stats from Anthropic API responses
2. Store in new `CacheTelemetryStore`
3. Report in `awos stats`

### Phase 2 (future)
- OpenAI/DeepSeek cache stats
- Gemini cache stats
- Per-component breakdown (worker vs planner)
- Cache hit rate as feature for LinUCB

---

## Implementation

### 1. New store: `cache_telemetry.py`

```python
@dataclass
class CacheEvent:
    timestamp: str
    component: str  # "worker", "planner", "critic"
    model: str
    input_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    cache_hit_rate: float  # cache_read / input_tokens
    cost_saved_usd: float

class CacheTelemetryStore:
    def __init__(self, store_path: str = ".awos"):
        self.file = Path(store_path) / "cache_stats.jsonl"
    
    def record(self, event: CacheEvent):
        # Append to jsonl
    
    def stats(self, since: Optional[datetime] = None) -> dict:
        # Return aggregate: total_hit_rate, tokens_cached, cost_saved
```

### 2. Extract from API response

**In `worker.py`, `planner.py`, `critic_engine.py`:**

```python
def _extract_cache_stats(response, component: str, model: str) -> CacheEvent:
    usage = response.get("usage", {})
    input_tokens = usage.get("input_tokens", 0)
    cache_read = usage.get("cache_read_input_tokens", 0)
    cache_create = usage.get("cache_creation_input_tokens", 0)
    
    hit_rate = cache_read / input_tokens if input_tokens > 0 else 0.0
    
    # Anthropic pricing: cached tokens 90% cheaper
    cost_saved = (cache_read * 0.9 * PRICE_PER_TOKEN[model])
    
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
```

**Wire into existing calls:**

```python
# In worker.execute_task():
response = self.client.messages.create(...)
cache_event = _extract_cache_stats(response, "worker", model_name)
cache_store.record(cache_event)
```

### 3. Update `awos stats`

**In `self_learning_metrics.py`:**

```python
def snapshot(self) -> MetricsSnapshot:
    # ... existing metrics ...
    
    cache_store = CacheTelemetryStore(self.store_path)
    cache_stats = cache_store.stats(since=datetime.now() - timedelta(days=7))
    
    return MetricsSnapshot(
        # ... existing fields ...
        cache_hit_rate=cache_stats["hit_rate"],
        cache_tokens_read=cache_stats["tokens_cached"],
        cache_cost_saved=cache_stats["cost_saved"],
    )
```

**Print in report:**

```python
def print_report(self):
    # ... existing output ...
    
    print(f"\n{'═'*60}")
    print("CACHE PERFORMANCE (last 7 days)")
    print(f"{'═'*60}")
    print(f"  Hit rate:      {self.cache_hit_rate:.1%}")
    print(f"  Cached tokens: {self.cache_tokens_read:,}")
    print(f"  Cost saved:    ${self.cache_cost_saved:.2f}")
```

---

## Testing

1. Unit test: `_extract_cache_stats` with mock response
2. Integration: Run `awos worker start "trivial goal"`, check `.awos/cache_stats.jsonl` created
3. Report: `awos stats` shows cache section

---

## Success criteria

- [ ] `CacheTelemetryStore` implemented
- [ ] Worker, Planner, Critic log cache events
- [ ] `awos stats` shows cache hit rate
- [ ] Unit tests pass
- [ ] Live proof: run long mission, observe ≥95% cache hit rate

---

## Files to change

| File | Change |
|------|--------|
| `scaffold/agent/cache_telemetry.py` | New store |
| `scaffold/agent/worker.py` | Extract + log cache stats |
| `scaffold/agent/planner.py` | Extract + log cache stats |
| `scaffold/agent/critic_engine.py` | Extract + log cache stats |
| `scaffold/agent/self_learning_metrics.py` | Add cache stats to snapshot + report |
| `tests/test_cache_telemetry.py` | Unit tests |
