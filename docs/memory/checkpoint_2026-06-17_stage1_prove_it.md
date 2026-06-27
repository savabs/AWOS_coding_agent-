# Checkpoint — Stage 1 Prove-It

**Date:** 2026-06-18  
**Research:** `docs/research/stage1_prove_it.md`  
**Spec:** `docs/specs/stage1_prove_it_spec.md`  
**Task:** `tasks/active/stage1_prove_it_task.md`

---

## Summary

Stage 1 prove-it batch **completed**. M1 learning gate fired accept. Worktree isolation **proved** with one follow-up bug noted.

---

## Phase A — M1 probation (5 validation runs)

**Command:** `python3 awos.py validate run --count 5`  
**Duration:** ~97s  
**Result:** 5/5 success (v1-16 … v1-20)

| Task | File | Status |
|------|------|--------|
| v1-16 | runtime_session.py | ✓ |
| v1-17 | learning_policy.py | ✓ |
| v1-18 | mcts_policy.py | ✓ |
| v1-19 | worktree.py | ✓ |
| v1-20 | token_tracker.py | ✓ |

**Learning outcome:**

- `sessions_completed`: 3 → **8**
- Candidate v1 probation: **5 sessions** with `used_candidate_prompt: true`
- Verdict: **ACCEPTED** → `active_version: 1`, `last_accepted_version: 1`
- Candidate KPI: 100% success, $0.001177/task avg (within cost gate)

**Cost:** All runs DeepSeek V4 Flash (cheap-only). Batch ~$0.006 tracked in learning_state.

---

## Phase B — Worktree live test

**Command:**
```bash
AWOS_USE_WORKTREE=true python3 awos.py run "…docstring on worktree_enabled…"
```

**Pass:**

- `[WORKTREE] Isolated execution: .awos/worktrees/bd85bb5e65d6`
- Session `rs_bd85bb5e65d6` sandbox: `enabled: true`, path persisted
- **Main repo** `scaffold/agent/virtual_execution_runtime.py` — **unchanged** (no docstring)
- Edit applied inside worktree copy only

**Issue (follow-up):**

- First worker attempt: `File not found` despite worktree containing full tree
- SymbolIndex logged `0 files` at start — likely indexing/path race in worktree
- Decomposition recovered but worktree file content diverged (worker recreated file incorrectly)
- Not a sandbox leak; isolation held. Fix: worktree cold-start file resolution / symbol index

---

## Artifacts

| Path | State |
|------|-------|
| `.awos/learning_state.json` | active v1, candidate null |
| `.awos/evolved_prompt.json` | v1 active guidelines |
| `.awos/v1_validation_progress.json` | 20/20 completed |
| `.awos/worktrees/bd85bb5e65d6/` | Preserved for review |
| `.awos/sessions/rs_bd85bb5e65d6.json` | completed |

---

## Next (Stage 1 remaining)

1. Fix worktree first-task file resolution (SymbolIndex 0 files in worktree)
2. Multi-task goal + pause/resume proof
3. Error pattern UNKNOWN typing
4. Verify-fail → replan
5. AWOS vs raw API benchmark

---

## Docs added

- `docs/research/stage1_prove_it.md`
- `docs/specs/stage1_prove_it_spec.md`
- `docs/v1_validation_tasks.json` — v1-16..20
- `tasks/active/stage1_prove_it_task.md`
