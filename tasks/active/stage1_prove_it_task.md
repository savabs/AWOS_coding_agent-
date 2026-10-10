# Stage 1 Prove-It — Task

**Research:** `docs/research/stage1_prove_it.md`  
**Spec:** `docs/specs/stage1_prove_it_spec.md`  
**Started:** 2026-06-17  
**Completed:** 2026-06-18  
**Checkpoint:** `docs/memory/checkpoint_2026-06-17_stage1_prove_it.md`

---

## Phase A — M1 probation (5 validation runs)

- [x] 1. Add v1-16..v1-20 to `docs/v1_validation_tasks.json`
- [x] 2. Confirm `.env` cost gates (`AWOS_CHEAP_ONLY`, `AWOS_PREMIUM_BUDGET`)
- [x] 3. Run `python3 awos.py validate run --count 5`
- [x] 4. Verify `.awos/learning_state.json` — candidate **accepted** at session 8
- [x] 5. Run `python3 awos.py stats` — v1 active

## Phase B — Worktree live test

- [x] 6. Run goal with `AWOS_USE_WORKTREE=true`
- [x] 7. Verify `[WORKTREE]` log + `.awos/worktrees/` + session sandbox JSON
- [ ] 7b. **Follow-up:** fix first-task file-not-found / SymbolIndex 0 in worktree

## Phase C — Closeout

- [x] 8. Update `memories/repo/project_structure.md` metrics
- [x] 9. Checkpoint `docs/memory/checkpoint_2026-06-17_stage1_prove_it.md`

---

## Pass criteria

| ID | Check | Result |
|----|-------|--------|
| A | 5/5 validation tasks succeed | ✓ |
| B | M1 candidate probation completes | ✓ accepted |
| C | Worktree isolation proved live | ✓ (with file-resolve bug) |
| D | Batch cost <$0.05 | ✓ |
