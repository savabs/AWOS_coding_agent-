# Checkpoint — Long Mission + PEI Benchmark

**Date:** 2026-06-23

---

## Long mission (`awos mission start --long`)

| Field | Value |
|-------|-------|
| Session | `rs_b2667c2e8eb7` |
| Status | **completed** |
| Plan | 8 pre-planned tasks (planner skipped) |
| Progress | Tasks 1–3, 5–8 + decompose `4_1`, `4_2` (task 4 needed sub-split) |
| Wall time | ~165s |
| Cost | $0.0158 |
| Worktree | `.awos/worktrees/b2667c2e8eb7` |
| Main repo | Edits in sandbox only |

**Win:** Fixed plan prevented 12→3 planner collapse. Multi-step run completed.

**Friction:** Task 4 (`verifier.py` comment-above edit) failed 3× then decomposed; critic kwargs bug logged.

---

## PEI benchmark (`scripts/benchmark_vs_raw_api.py`)

| Metric | Raw API | AWOS harness |
|--------|---------|--------------|
| Pass rate | 67% (2/3) | **100%** (3/3) |
| Total cost | $0.0006 | $0.0010 |
| Verified fixes / $ | 3129 | 2938 |

**Key case:** `02_wrong_logic` — raw FAIL, AWOS PASS at same cost.

**Artifacts:**
- `.awos/benchmarks/benchmark_vs_raw_20260623_061847.json`
- `docs/product/pei_proof.md`

---

## Next

1. Fix task-4 class of edits (comment-above in large files) or swap plan step 4 for easier file
2. Fix `CriticEngine.critique(codebase_context=...)` mismatch
3. Phase C: `awos worker start|status` thin wrapper
