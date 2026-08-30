---
title: "Multi-Resolution Context Pipeline — Proof Guide"
tags:
  - doc/proof
  - topic/multi-resolution
  - phase/4
---

> **Content:** [multi_resolution_context_proof.html](multi_resolution_context_proof.html) — open in browser.

# Multi-Resolution Context Pipeline — Proof Guide

## Command

```bash
bash scripts/demo_multi_resolution_context.sh
```

## Success Markers

Watch for these in the output:

| # | Marker | What it means |
|---|--------|---------------|
| 1 | `[PASS] semantic_retriever imports OK` | New module imports cleanly |
| 2 | `Level 0: 18 tokens  (99% savings vs raw)` | Task-only context is tiny |
| 3 | `Level 2: 466 tokens  (96% savings vs raw)` | Signatures level is much cheaper than full file |
| 4 | `[PASS] suggest_level(...)` for all 4 cases | Heuristic returns correct levels |
| 5 | `29 passed` + `All checks passed!` | Tests + lint clean |

## Failure Signals

- Any `FAIL` or `ERROR` in output
- Level N+1 has fewer tokens than Level N
- Level clamping not working (e.g., Level 99 → not 3)
- Missing file or empty task crashes

## Observed (2026-07-08)

```
Command: bash scripts/demo_multi_resolution_context.sh

Output:
  Level 0:    18 tokens  ( 99% savings vs raw)  [task]
  Level 1:    95 tokens  ( 99% savings vs raw)  [task, module]
  Level 2:   466 tokens  ( 96% savings vs raw)  [task, module, sigs]
  Level 3:   981 tokens  ( 93% savings vs raw)  [task, module, sigs, file]

  suggest_level -> 2 (complexity=medium -> expect 2)

  Missing file:       Level 3, 3 tokens (no crash)
  Empty task:         Level 0, task="TASK: " (no crash)
  Level 99 clamped:   Level 3 (expect 3)
  Level -5 clamped:   Level 0 (expect 0)

  29 passed in 0.11s
  All checks passed!
```

## Related

- [[multi_resolution_context_spec]] — spec
- [[multi_resolution_context_task]] — task file
