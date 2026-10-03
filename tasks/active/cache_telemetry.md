# Cache Hit Telemetry

**Research:** `docs/research/cache_telemetry.md`  
**Spec:** `docs/specs/cache_telemetry_spec.md`

**Status:** IN PROGRESS

---

## Steps

- [x] Research doc
- [x] Spec doc
- [x] Implement `CacheTelemetryStore` in `cache_telemetry.py`
- [x] Add `_extract_cache_stats()` helper
- [x] Wire into `worker.py`
- [x] Wire into `planner.py`
- [x] Wire into `critic_engine.py`
- [x] Update `self_learning_metrics.py` (snapshot + report)
- [x] Unit tests
- [x] Smoke test: run trivial goal, check `.awos/cache_stats.jsonl`
- [ ] Live proof: long mission, verify ≥95% cache hit rate
- [x] Update `awos stats` to show cache section

---

## Implementation order

1. Store + helper (isolated, testable)
2. Wire into one component (worker) — smoke test
3. Wire into remaining components (planner, critic)
4. Report integration (`awos stats`)
5. Long mission proof
