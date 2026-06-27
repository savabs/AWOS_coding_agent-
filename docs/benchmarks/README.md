# AWOS Benchmarks

Frozen baselines and comparison guides for measuring harness improvements over time.

---

## Files

| File | Purpose |
|------|---------|
| [`BASELINE.md`](BASELINE.md) | Human-readable baseline v1 summary |
| [`baseline_v1_2026-06-25.json`](baseline_v1_2026-06-25.json) | Machine-readable frozen metrics |
| [`../product/pei_proof.md`](../product/pei_proof.md) | Latest PEI run output (regenerated) |
| [`../research/baseline_benchmark.md`](../research/baseline_benchmark.md) | Why we baseline + design |
| [`../research/benchmark_vs_raw_api.md`](../research/benchmark_vs_raw_api.md) | PEI suite methodology |

**Live run artifacts:** `.awos/benchmarks/benchmark_vs_raw_<timestamp>.json`

---

## Quick commands

```bash
# Full baseline re-run (PEI live + unit tests)
./scripts/run_baseline_benchmark.sh

# PEI only (needs API keys)
python3 scripts/benchmark_vs_raw_api.py

# PEI dry-run (no API)
python3 scripts/benchmark_vs_raw_api.py --dry-run

# Missions (separate from PEI — manual log to compare)
python3 awos.py mission start --long
python3 awos.py mission start --clawcode

# Compare latest PEI JSON to baseline v1
python3 scripts/compare_baseline.py
```

---

## Comparison workflow

1. Run `./scripts/run_baseline_benchmark.sh` on **current** code
2. Run `python3 scripts/compare_baseline.py` — prints delta vs v1
3. If harness semantics changed (e.g. Outcome Judge), freeze `baseline_v2_*.json`
4. Note results in checkpoint: `docs/memory/checkpoint_YYYY-MM-DD_baseline_compare.md`

---

## Suites overview

| Suite | What it measures | Baseline v1 |
|-------|------------------|-------------|
| `pei_bug_fix` | AWOS vs raw API on bugs | 100% vs 67% |
| `long_mission_awos` | 8-task self-repo run | 8/8, 165s |
| `phase_d_clawcode` | 14-task external repo | 14/14, 40.5s |
| `cache_telemetry` | Cache hit rate | 1.7% |
| `unit_tests` | Regression gate | 776 pass |
