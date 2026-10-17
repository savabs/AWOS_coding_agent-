# Code Evolution Lab — AWOS vs Raw API

**Generated:** 2026-06-27T05:40:08.954071+00:00

Multi-commit maintenance test: 5 commits, 12 → 23 tests, regression tracking.

## Summary

| | Raw API | AWOS Harness |
|--|--|--|
| **Zero-Regression Rate** | yes | yes |
| Final 23/23 tests pass? | no | no |
| EvoScore (γ=1.5) | 0.9796 | 0.9796 |
| Total cost | $0.0000 | $0.0000 |
| Total time | 0.0s | 0.0s |

**Winner (maintainability):** tie

## What This Proves

- **Raw API** = one-shot fixes per commit, no verify loop
- **AWOS** = verify loop catches regressions before moving forward

**Zero-Regression Rate** = did agent break ANY previously-passing test?

This is the SWE-CI metric: 75% of agents fail this across long-term evolution.

## Per-Commit Detail

### Raw API

| Commit | Pass | Fail | Regressions | Cost | Time |
|--------|------|------|-------------|------|------|
| commit_1_tiers | 15 | 0 | **0** | $0.0000 | 0.0s |
| commit_2_refactor | 17 | 0 | **0** | $0.0000 | 0.0s |
| commit_3_bulk | 20 | 0 | **0** | $0.0000 | 0.0s |
| commit_4_integration | 22 | 1 | **0** | $0.0000 | 0.0s |

### AWOS Harness

| Commit | Pass | Fail | Regressions | Cost | Time |
|--------|------|------|-------------|------|------|
| commit_1_tiers | 15 | 0 | **0** | $0.0000 | 0.0s |
| commit_2_refactor | 17 | 0 | **0** | $0.0000 | 0.0s |
| commit_3_bulk | 20 | 0 | **0** | $0.0000 | 0.0s |
| commit_4_integration | 22 | 1 | **0** | $0.0000 | 0.0s |

Raw JSON: `.awos/benchmarks/code_evolution_head_to_head_20260627_054008.json`
