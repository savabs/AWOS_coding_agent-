# PEI Proof — AWOS vs Raw API

**Generated:** 2026-07-27T09:16:35.397090+00:00

Fixed bug-fix cases in isolated temp dirs. Same cheap model tier (`AWOS_CHEAP_ONLY=true`).

## Summary

| Metric | Raw API | AWOS harness |
|--------|---------|--------------|
| Pass rate | 0% | 100% |
| Total cost | $0.0000 | $0.0219 |
| Verified fixes / $ | 0.00 | 45.77 |

## Per case

| Case | Raw | AWOS | Winner |
|------|-----|------|--------|
| 02_wrong_logic | ✗ $0.0000 | ✓ $0.0219 | AWOS |

Raw JSON: `.awos/benchmarks/benchmark_vs_raw_20260727_091724.json`

See also: `docs/product/stage1_subscription_worker_guideline.md`
