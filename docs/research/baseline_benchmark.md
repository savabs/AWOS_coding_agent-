# Research — Baseline Benchmark (version comparison)

**Date:** 2026-06-25  
**Context:** Need frozen metrics for current AWOS to compare against future harness improvements (Outcome Judge, Value Proof Engine, verified learning loop).

---

## Problem

We have scattered proof artifacts:

- PEI benchmark JSON (2026-06-23)
- Long mission checkpoint (8 tasks, AWOS repo)
- Phase D clawcode (14 tasks, external repo)
- System rating doc (7.5/10)
- Cache telemetry

Without a **single frozen baseline**, we cannot answer:

> "Did the June update actually make AWOS better, or did we just change the demo?"

---

## What a baseline must capture

| Layer | Metric | Why |
|-------|--------|-----|
| **Regression** | Unit test count / pass | Code didn't break |
| **PEI suite** | Raw vs AWOS pass rate, cost | Core value claim |
| **Mission suite** | Tasks completed, wall time, cost | Long-run reliability |
| **External repo** | clawcode mission stats | Stranger-repo proof |
| **Telemetry** | Cache hit rate, cost/task | Efficiency trend |
| **Provenance** | git commit, env flags, date | Reproducibility |

---

## Design principles

1. **Frozen manifest** — `docs/benchmarks/baseline_v1_*.json` never edited; new version = new file  
2. **Re-runnable commands** — `scripts/run_baseline_benchmark.sh` documents exact steps  
3. **Compare, don't replace** — future runs write to `.awos/benchmarks/` and diff against baseline  
4. **Honest scope** — baseline v1 notes known gaps (no semantic judge, 3 bug cases only, README false-pass risk)

---

## Suites in baseline v1

| Suite ID | Command | Primary metric |
|----------|---------|----------------|
| `pei_bug_fix` | `python3 scripts/benchmark_vs_raw_api.py` | AWOS pass rate vs raw |
| `long_mission_awos` | `awos mission start --long` | 8/8 tasks, wall time |
| `phase_d_clawcode` | `awos mission start --clawcode` | 14/14 external repo |
| `cache_telemetry` | `awos stats` | hit rate, tokens |
| `unit_tests` | `pytest tests/` | pass count |

---

## Future: automated compare

```bash
python3 scripts/compare_baseline.py --baseline docs/benchmarks/baseline_v1_2026-06-25.json
```

Outputs delta table: pass rate Δ, cost Δ, regression flags.

---

## Related

- `docs/research/benchmark_vs_raw_api.md`
- `docs/research/cache_telemetry.md`
- `docs/assessment/system_rating_2026-06-24.md`
- Upcoming: Outcome Judge / Value Proof Engine
