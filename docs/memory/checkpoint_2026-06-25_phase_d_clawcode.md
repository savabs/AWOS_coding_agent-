# Checkpoint — Phase D clawcode Real Repo Proof

**Date:** 2026-06-25  
**Command:** `python3 awos.py mission start --clawcode`

---

## Observed output

```
Mission: Phase D — Real Repo Proof (clawcode)
Target:  /home/becmachlean/2024/projects/clawcode
[MISSION] Pre-planned task list: 14 tasks (planner skipped)
[WORKTREE] Isolated sandbox: .../clawcode/.awos/worktrees/34d1a845a6b0

Tasks Completed: 14/14
Tasks Failed:    0/14
Time Elapsed:    40.5s
Total Cost:      $0.0070
Session:         rs_34d1a845a6b0 [completed]
```

**Cache stats (`awos stats`):** 1.7% hit rate, 1,664 cached tokens, 97,849 fresh tokens

---

## Success criteria

| Criterion | Result |
|-----------|--------|
| 12+ of 14 tasks | ✅ 14/14 |
| External repo (not AWOS) | ✅ clawcode |
| Worktree isolation | ✅ sandbox path under clawcode/.awos/worktrees/ |
| Session completed | ✅ rs_34d1a845a6b0 |
| Cache telemetry | ✅ recorded |
| Wall time 30+ min | ❌ 40.5s (fast run; pause/resume not exercised) |

---

## Review

```bash
awos worker diff
cd /home/becmachlean/2024/projects/clawcode/.awos/worktrees/34d1a845a6b0
python3 -m pytest tests/ -q
```

---

## What this proves

First **real external repo** mission: AWOS executed 14 verified docstring/README edits on clawcode in an isolated worktree with zero failures and sub-cent cost.

**Still open for subscription bar:** longer wall-time run (30–120 min) and pause/resume drill on external repo.
