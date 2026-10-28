# Checkpoint — `awos worker` CLI (Phase C)

**Date:** 2026-06-24

---

## What shipped

**`awos worker` CLI** — simple product surface that hides kernel jargon (mission/gauntlet/sessions).

### Commands

| Command | What it does |
|---------|--------------|
| `awos worker start "<goal>"` | Start work on a goal (maps to orchestrator + runtime session) |
| `awos worker status` | List all sessions (clean UX, shows sandbox paths) |
| `awos worker resume [id]` | Resume paused work (defaults to latest paused) |
| `awos worker diff [id]` | Show sandbox changes vs main (defaults to latest) |
| `awos worker cancel [id]` | Cancel active/paused work (defaults to latest active) |

### Files changed

| File | Change |
|------|--------|
| `awos.py` | Added 5 `cmd_worker_*` functions, registered `worker` subparser, wired handler in `main()` |

**Zero kernel changes** — pure UX wrapper over existing orchestrator + sessions.

---

## What it looks like

```bash
$ awos worker status

40 session(s):

rs_b2667c2e8eb7  completed        9/8 tasks  $0.000  "Stage 1 long workload mission: eight doc..."
  └─ sandbox: .awos/worktrees/b2667c2e8eb7
rs_725e7fc852e2  completed        1/1 tasks  $0.000  "Fix broken_add to return a + b instead o..."
...
```

```bash
$ awos worker diff rs_b2667c2e8eb7

Session: rs_b2667c2e8eb7
Sandbox: .awos/worktrees/b2667c2e8eb7

Changes vs main:

diff --git a/AGENTS.md b/AGENTS.md
...
```

---

## Impact on system rating

**Before:** UX = 3/10 (jargon exposed: mission/gauntlet/sessions)  
**After:** UX = 8/10 (simple verbs: start/status/resume/diff/cancel)

**Overall rating:** 6.5 → **7.5/10** (lab-grade → early product)

---

## What's left for subscription-worthy (8.5+/10)

| Gap | Next step |
|-----|-----------|
| **Multi-hour proof** | One mission that runs 30–120 min unattended |
| **Real repo proof** | Run on 3+ stranger repos (not toy fixtures) |
| **Planner reliability** | Fix collapse without `plan_file` workaround |
| **Documentation** | Update README, guideline to show `worker` as primary |
| **Billing path** | Stripe integration (deferred until above repeat) |

---

## Next session

1. Update `README.md` — show `awos worker` as primary interface
2. Update `docs/product/stage1_subscription_worker_guideline.md` — Phase C done
3. Update `awos.py` docstring — show `worker` commands first
4. **Then:** run one multi-hour real mission to raise wall-time rating

---

## Related

- Task: `tasks/active/awos_worker_cli.md` (mark done)
- Rating: `docs/assessment/system_rating_2026-06-24.md` (update)
- Guideline: `docs/product/stage1_subscription_worker_guideline.md` (update Phase C status)
