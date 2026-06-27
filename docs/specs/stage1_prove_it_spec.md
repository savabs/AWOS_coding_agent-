---
title: Stage 1 Prove-It — Spec
tags:
  - spec
  - stage/1
---

# Stage 1 Prove-It — Spec

**Research:** [`docs/research/stage1_prove_it.md`](../research/stage1_prove_it.md)  
**Task tracker:** `tasks/active/stage1_prove_it_task.md`

---

## Scope

Prove M1 candidate probation and worktree sandbox on live runs. No new kernel features.

---

## Phase A — Validation batch (M1 probation)

### A1. Extend task queue

Add tasks `v1-16` … `v1-20` to `docs/v1_validation_tasks.json`.  
Set `target_count` to 20.

### A2. Run batch

```bash
python3 awos.py validate run --count 5
```

Environment (from `.env`):

- `AWOS_CHEAP_ONLY=true`
- `AWOS_PREMIUM_BUDGET=0`
- `AWOS_MONTHLY_BUDGET=20`

### A3. Verify learning

```bash
python3 awos.py stats
cat .awos/learning_state.json
```

**Pass:** `sessions_completed >= 8`; candidate sessions recorded; verdict `accepted` or `rejected` OR `sessions_evaluated >= 5`.

---

## Phase B — Worktree live test

### B1. Pre-check

- Repo is git worktree (`git rev-parse --is-inside-work-tree`)
- `AWOS_USE_WORKTREE=true`

### B2. Run

```bash
AWOS_USE_WORKTREE=true python3 awos.py run \
  "In scaffold/agent/virtual_execution_runtime.py add a one-line docstring to worktree_enabled explaining it returns True when AWOS_USE_WORKTREE is enabled"
```

### B3. Verify

1. Stdout contains `[WORKTREE] Isolated execution:`
2. Latest `rs_*.json` has `sandbox.enabled: true` and non-empty `worktree_path`
3. Path `.awos/worktrees/<feature_id>/` exists
4. Edit applied inside worktree (or branch diff visible)

**Pass:** B3.1–B3.3 true. B3.4 best-effort.

---

## Phase C — Metrics update

Update `memories/repo/project_structure.md` metrics only:

- M1 PromptEvolver live status
- v2 validation batch results
- Worktree live test status

---

## Out of scope

- Multi-task pause/resume
- MCTS trace forcing
- Error pattern classifier fix
- Code changes beyond validation task JSON
