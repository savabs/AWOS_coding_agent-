# P2 Spec — Prompt Cache, Parallel Sampling, Worktree, Condensed History

**Full spec**: [docs/specs/p2_performance_spec.html](p2_performance_spec.html)  
**Priority**: P2 Medium | **Effort**: 11h | **Prerequisite**: P1 stable

## What
Module-level Planner singleton for prompt caching (75% cost reduction). Parallel sampling for L3+ tasks. Git worktree isolation. Condensed ACI-style task history.

## Steps (8)
1. Planner singleton + get_planner() + goal complexity routing (1.5h)
2. Update orchestrator to use get_planner() (0.5h)
3. PatchCandidate dataclass + _sample_candidates() (2h)
4. _score_candidate() + orchestration gating (1.5h)
5. WorktreeManager class (2.5h)
6. Wire worktree into execute_feature() opt-in (0.5h)
7. Condensed history builder + worker prompt injection (1h)
8. Write tests/test_p2_improvements.py (13 tests) (1.5h)

## Files
- scaffold/agent/planner.py — singleton
- scaffold/agent/orchestrator.py — sampling, worktree, history
- scaffold/agent/worker.py — sampling
- scaffold/agent/worktree.py (new) — WorktreeManager
- tests/test_p2_improvements.py — new test file
