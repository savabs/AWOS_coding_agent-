# AWOS Baseline Benchmark — v1 (frozen 2026-06-25)

> **Purpose:** Canonical snapshot of Stage 1 AWOS **before** Outcome Judge / Value Proof Engine.  
> Compare all future improvements against this file — do not overwrite.

**Machine-readable:** [`baseline_v1_2026-06-25.json`](baseline_v1_2026-06-25.json)  
**Git commit at freeze:** `b644839`  
**Research:** [`docs/research/baseline_benchmark.md`](../research/baseline_benchmark.md)

---

## How to use this baseline

```bash
# Re-run PEI suite (live — needs API keys, ~2–5 min)
./scripts/run_baseline_benchmark.sh

# Compare latest JSON to frozen v1 (when compare script exists)
python3 scripts/compare_baseline.py
```

When you ship a harness change, run the suite again and check:

| Question | Where to look |
|----------|----------------|
| Did pass rate improve? | `pei_bug_fix.awos.pass_rate` |
| Did cost per fix drop? | `awos.verified_per_dollar` |
| Did we break missions? | long + clawcode task counts |
| Regression? | `pytest tests/` |

Create **`baseline_v2_YYYY-MM-DD.json`** when semantics change (e.g. Outcome Judge added) — don't mutate v1.

---

## Suite 1 — PEI bug-fix benchmark

**Command:** `python3 scripts/benchmark_vs_raw_api.py`  
**Config:** `tests/fixtures/benchmark_goals.json` (3 cases)  
**Artifact:** `.awos/benchmarks/benchmark_vs_raw_20260623_061847.json`

| Metric | Raw API (1-shot) | AWOS harness |
|--------|------------------|--------------|
| Pass rate | **67%** (2/3) | **100%** (3/3) |
| Total cost | $0.00064 | $0.00102 |
| Verified fixes / $ | 3129 | 2938 |
| Total wall time | ~17s | ~69s |

**Per case:**

| Case | Raw | AWOS | Winner |
|------|-----|------|--------|
| `01_off_by_one` | ✓ $0.00017 | ✓ $0.00036 | tie (raw cheaper) |
| `02_wrong_logic` | ✗ $0.00036 | ✓ $0.00043 | **AWOS** |
| `03_simple_add` | ✓ $0.00011 | ✓ $0.00023 | tie (raw cheaper) |

**Interpretation:** Harness wins on **hard case** via verify/retry loop. On easy cases raw is faster and cheaper. Subscription claim needs more cases + semantic checks.

---

## Suite 2 — Long mission (AWOS repo)

**Command:** `awos mission start --long`  
**Date:** 2026-06-23 · **Session:** `rs_b2667c2e8eb7`

| Metric | Value |
|--------|-------|
| Tasks | 8/8 completed |
| Wall time | ~165s |
| Cost | $0.0158 |
| Repo | AWOS (self-edit) |
| Notable | Task 4 decomposed after failures |

---

## Suite 3 — Phase D clawcode (external repo)

**Command:** `awos mission start --clawcode`  
**Date:** 2026-06-25 · **Session:** `rs_34d1a845a6b0`

| Metric | Value |
|--------|-------|
| Tasks | 14/14 completed (mechanical) |
| Wall time | 40.5s |
| Cost | $0.0070 |
| Repo | clawcode (external) |
| pytest in worktree | 22/22 passed |
| Semantic audit | ~13/14 est.; README task likely false-green |

**Interpretation:** Proves external-repo plumbing. **Not** proof of hard coding quality until Outcome Judge runs.

---

## Suite 4 — Cache telemetry

**Command:** `awos stats` (after Phase D runs)

| Metric | Value |
|--------|-------|
| Hit rate (7d) | 1.7% |
| Cached tokens | 1,664 |
| Fresh tokens | 97,849 |

Worker-heavy DeepSeek runs; 95% cache target not met on this workload.

---

## Suite 5 — Unit tests

| Metric | Value |
|--------|-------|
| Passing | 776 (as of 2026-06-08) |
| Flaky | 1 |

Re-run `pytest tests/` before any baseline comparison.

---

## System rating at baseline

**7.5 / 10** (early product, lab-proven) — [`docs/assessment/system_rating_2026-06-24.md`](../assessment/system_rating_2026-06-24.md)

---

## Known gaps (baseline v1 honesty)

- Mechanical verifier only — no semantic goal assertions
- Only 3 PEI bug cases
- No head-to-head vs raw 3-shot retry
- clawcode tasks were trivial docstrings
- No multi-hour unattended proof

---

## When to freeze baseline v2

Freeze a new baseline when **any** of these ship:

1. Outcome Judge + anti-hack guard
2. Value Proof corpus ≥ 10 cases with semantic_pass in JSON
3. Major orchestrator / verifier architecture change

---

## Changelog

| Version | Date | Notes |
|---------|------|-------|
| v1 | 2026-06-25 | Initial freeze: PEI + long mission + clawcode + cache |
