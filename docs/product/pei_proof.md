# PEI Proof — AWOS vs Raw API

**Generated:** 2026-06-23T06:16:48.504651+00:00

Fixed bug-fix cases in isolated temp dirs. Same cheap model tier (`AWOS_CHEAP_ONLY=true`).

## Summary

| Metric | Raw API | AWOS harness |
|--------|---------|--------------|
| Pass rate | 67% | 100% |
| Total cost | $0.0006 | $0.0010 |
| Verified fixes / $ | 3129.89 | 2938.30 |

## Per case

| Case | Raw | AWOS | Winner |
|------|-----|------|--------|
| 01_off_by_one | ✓ $0.0002 | ✓ $0.0004 | tie |
| 02_wrong_logic | ✗ $0.0004 | ✓ $0.0004 | AWOS |
| 03_simple_add | ✓ $0.0001 | ✓ $0.0002 | tie |

Raw JSON: `.awos/benchmarks/benchmark_vs_raw_20260623_061847.json`

See also: `docs/product/stage1_subscription_worker_guideline.md`
