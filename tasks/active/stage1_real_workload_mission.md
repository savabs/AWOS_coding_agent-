# Stage 1 Real Workload Mission

**Status:** COMPLETE (G4 verified 2026-06-19)  
**Spec:** `docs/specs/stage1_real_workload_mission.md`  
**Config:** `docs/missions/stage1_real_workload.json`

---

## Checklist

- [x] Wire `VirtualExecutionRuntime` into `orchestrator.execute_feature`
- [x] `awos mission guide|start|status` CLI
- [x] **Run mission start** — `rs_77134f119597` (2026-06-19)
- [x] **Pause** — SIGINT after task 2 → `rs_181d75a7fd91` paused with [1,2,3] (task 3 finished before checkpoint)
- [x] **Resume** — `awos sessions resume rs_181d75a7fd91` → tasks 4–5 done (after `register_worktree` fix)
- [x] **Complete** — session `completed` 4/4 tasks, $0.0041, 34.1s (ran through without pause)
- [x] **Verify main repo** — mission edits in worktree only; main differs from `.awos/worktrees/181d75a7fd91` (G4 pass)
- [x] Update gauntlet booklet + checkpoint

---

## Commands

```bash
awos mission start
awos sessions list --status paused
awos sessions show rs_<id>
awos sessions resume rs_<id>
git status   # main repo should be clean
ls .awos/worktrees/
```

---

## Notes

### Tranche 1 — 2026-06-19

| Field | Value |
|-------|-------|
| Session | `rs_77134f119597` |
| Worktree | `.awos/worktrees/77134f119597` |
| Tasks | 4/4 (planner dropped task 5 README; goal had 5 items) |
| Wall time | 34.1s |
| Cost | $0.0041 |
| Pause/resume | Not exercised — tasks finished before manual Ctrl+C |

Edits verified in worktree: `mcts_policy.py`, `install-awos-cli.sh`, `test_mcts_trace_store.py`, `awos.py` (Sandbox line). SymbolIndex reported 0 files at start.

**Next:** Re-run with pause after task 2, or add a heavier goal, to prove pause → resume path on this mission.

### Tranche 2 — pause/resume — 2026-06-19

| Field | Value |
|-------|-------|
| Session | `rs_181d75a7fd91` |
| Pause | SIGINT after task 2; paused with tasks [1,2,3] (task 3 in-flight completed first) |
| Bug fixed | `WorktreeManager.register_worktree()` missing — blocked first resume |
| Resume | tasks 4–5 completed |
| Final | **completed** 5/5 tasks |
