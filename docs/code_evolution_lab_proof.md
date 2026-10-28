# Code Evolution Lab — Proof Guide

**Spec:** `docs/specs/code_evolution_lab_spec.md`  
**Task:** `tasks/active/code_evolution_lab.md`

## What This Proves

Agent maintains code across **5 sequential commits** without breaking earlier green tests.

Based on **SWE-CI research**: 75% of agents break working code during multi-commit evolution.

## Fixture

`tests/fixtures/wedge_v1/code_evolution/` — e-commerce order system evolving from baseline → tiered discounts → refactor → bulk orders → integration.

| Commit | Tests | Baseline Green | New Failures |
|--------|-------|----------------|--------------|
| 0 (baseline) | 12 | 12 | 0 |
| 1 (tiers) | 15 | 12 | 3 |
| 2 (refactor) | 17 | 15 | 2 |
| 3 (bulk) | 20 | 17 | 3 (includes carry-forward) |
| 4 (integration) | 23 | 17 | 6 (accumulated + integration) |

**Goal:** Agent fixes each commit **without breaking baseline tests** → Zero-Regression Rate = 100%

## Run

### Fixture smoke test (no API)

```bash
./scripts/demo_code_evolution_lab.sh
pytest tests/test_code_evolution_corpus.py -q
```

**Success markers:**
- `commit_0_baseline`: `12 passed`
- `commit_1_tiers`: `3 failed, 12 passed`
- `commit_2_refactor`: `2 failed, 15 passed`
- `commit_3_bulk`: `4 failed, 16 passed`
- `commit_4_integration`: `6 failed, 17 passed`

### Head-to-head benchmark (API calls, ~$0.10, 5-10 min)

```bash
python3 scripts/benchmark_code_evolution_lab.py
```

**Success markers:**
- `[RAW] Running multi-commit evolution...`
- `[AWOS] Running multi-commit evolution...`
- Final report: `docs/product/code_evolution_head_to_head.md`
- JSON: `.awos/benchmarks/code_evolution_head_to_head_*.json`

## Metrics

| Metric | Definition |
|--------|------------|
| **Zero-Regression Rate** | 1 if no previously-passing test ever fails; else 0 |
| **EvoScore** | Future-weighted normalized change (γ=1.5) — rewards sustained stability |
| **Final Pass** | All 23 tests green at commit_4 |

## Interpretation

- **Both zero-regr=True, final=True:** fixture too easy, agent value unclear
- **Raw zero-regr=False, AWOS zero-regr=True:** **AWOS wins** — harness prevents regressions
- **Both zero-regr=False:** fixture hard enough, compare regression counts + EvoScore

---

## Status (2026-06-27)

**Fixture complete:** 5 commits, golden fixes, corpus tests, smoke demo.

**Benchmark script:** Built, runs golden-copy simulation (not actual worker/verifier yet).

**Next:** Wire actual agent calls to demonstrate regression-catching or run on real repo evolution.
