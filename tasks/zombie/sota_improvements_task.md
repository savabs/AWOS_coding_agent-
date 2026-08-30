# AWOS SOTA Improvements — Master Task

**Full task**: [tasks/active/sota_improvements_task.html](sota_improvements_task.html)  
**Created**: 2026-05-22 | **Total**: 31 steps, 39h

## Status: PLANNED — Starting with P0

## Tiers
- 🔴 P0 (7h): Edit reliability — JSON format, read-before-write, multi-match detection
- 🟠 P1 (9h): Verification — pytest execution, architect+editor, context compaction  
- 🟡 P2 (11h): Performance — prompt cache, parallel sampling, worktree, condensed history
- 🟣 P3 (12h): SOTA — ruff lint, auto skills, call graph

## Rules
- P0 must complete before P1
- P1 must be stable before P2 or P3
- Run full pytest after each tier — zero regressions
- Gate P2.2 (parallel sampling) behind AWOS_PARALLEL_SAMPLING=true
- Gate P2.3 (worktree) behind AWOS_USE_WORKTREE=true

## Spec Links
- Research: docs/research/sota_improvements.html
- P0: docs/specs/p0_structured_edit_spec.html
- P1: docs/specs/p1_verifier_architect_spec.html
- P2: docs/specs/p2_performance_spec.html
- P3: docs/specs/p3_advanced_spec.html
