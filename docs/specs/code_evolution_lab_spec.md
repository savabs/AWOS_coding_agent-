# Code Evolution Lab — Spec (v0.1)

**Research:** `docs/research/code_evolution_lab_design.md`  
**Task:** `tasks/active/code_evolution_lab.md`

## Purpose

Lightweight SWE-CI-style test: can an agent fix new failures **without breaking earlier green tests**?

## Metrics

| Metric | Definition |
|--------|------------|
| **Zero-Regression Rate** | 1 if no previously-passing test ever fails; else 0 |
| **Final pass** | All tests green at final commit |
| **EvoScore** | Future-weighted normalized change (γ=1.5) |

## Fixture

`tests/fixtures/wedge_v1/code_evolution/` — sequential commit snapshots.

## Live proof (when complete)

```bash
./scripts/demo_code_evolution_lab.sh          # fixture smoke
python3 scripts/benchmark_code_evolution_lab.py  # raw vs AWOS
```

Success markers:
- `commit_0`: `12 passed`
- Final stage: `21 passed`
- Report: `docs/product/code_evolution_head_to_head.md`
- AWOS zero-regression vs raw introducing regressions

## Status

**Partial (hour 1):** commits 0–2 built and verified. Commits 3–4 + benchmark pending.
