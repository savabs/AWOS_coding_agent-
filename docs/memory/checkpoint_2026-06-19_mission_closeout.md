# Checkpoint — Stage 1 Mission Closeout

**Date:** 2026-06-19

---

## Summary

Real workload mission **complete** with pause/resume proof. G4 worktree isolation signed off. SymbolIndex worktree bug fixed and G1 gauntlet re-passed live.

---

## Mission results

| Session | Result | Notes |
|---------|--------|-------|
| `rs_77134f119597` | completed 4/4 | First tranche; no pause |
| `rs_181d75a7fd91` | completed 5/5 | SIGINT after task 2 → paused [1,2,3] → resume tasks 4–5 |

**G4:** Mission file edits (`mcts_policy.py`, `awos.py`, `README.md`, etc.) differ between main tree and `.awos/worktrees/181d75a7fd91` — isolation confirmed.

---

## Kernel fixes (this session)

1. **`WorktreeManager.register_worktree()`** — resume could not reattach existing sandbox (`AttributeError`).
2. **`should_index_py_file()`** in `symbol_index.py` — filter by path relative to codebase root; fixes `SymbolIndex: 0 files` inside `.awos/worktrees/`.

**Live SymbolIndex:** `80 files, 974 symbols` on worktree `181d75a7fd91` (was `0 files`).

**G1 gauntlet:** `awos gauntlet run G1` → **PASS** (`rs_2a7a76d29201`).

---

## Gauntlet checklist (updated)

| Item | Status |
|------|--------|
| G1 worktree + SymbolIndex > 0 | ✅ |
| G2 pause/resume | ✅ |
| G3 MCTS | ✅ |
| G4 main-repo clean | ✅ |
| Sandbox line in `awos sessions show` | ✅ |
| Typed error (not UNKNOWN) | ✅ G3 → `WORKER_FAIL` |

---

## Still open (Stage 1 exit)

- Multi-hour wall-clock run
- Benchmark vs raw API

**Verify-fail → replan:** ✅ G7 + `tests/test_verify_fail_replan.py` (2026-06-21).

---

## Key paths

- Mission config: `docs/missions/stage1_real_workload.json`
- Task tracker: `tasks/active/stage1_real_workload_mission.md`
- Booklet: `docs/stage1_risk_gauntlet_booklet.md`
