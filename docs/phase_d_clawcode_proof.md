# Phase D Proof — clawcode Real Repo Mission

**Mission config:** `docs/missions/stage1_phase_d_clawcode.json`  
**Task plan:** `docs/missions/stage1_phase_d_clawcode.plan.json` (14 tasks)

---

## What this proves

AWOS running on a **real external Python repo** (not AWOS itself):

- Multi-step execution with pre-planned tasks
- Worktree sandbox isolation on stranger repo
- Verifier + pytest gating
- Session pause/resume
- Cache telemetry + cost tracking

---

## Quick start

```bash
./scripts/demo_phase_d_clawcode.sh
```

Or manually:

```bash
python3 awos.py mission start --clawcode
```

---

## Watch for

| Marker | Meaning |
|--------|---------|
| `Target: /home/becmachlean/2024/projects/clawcode` | External repo wired |
| `[MISSION] Pre-planned task list: 14 tasks` | Planner bypassed |
| `[WORKTREE] Isolated sandbox:` | Edits isolated from main |
| `[TASK N] VERIFIED` | Verifier passed |
| `Tasks Completed: 12+/14` | Mission mostly done |
| `Cache Performance` in `awos stats` | Telemetry captured |

---

## Success criteria

- [ ] 12+ of 14 tasks completed
- [ ] `git status` clean on clawcode main branch
- [ ] `pytest tests/ -q` green in worktree
- [ ] Session `rs_*` status `completed` or clean `paused` with resume path
- [ ] `awos stats` shows cache section with events from run

---

## Pause / resume drill (optional)

1. Start mission
2. Ctrl+C after task 6–8
3. `awos mission status --clawcode`
4. `awos sessions resume rs_<id>`
5. Mission continues from checkpoint

---

## Review before merge

```bash
awos worker diff
cd /home/becmachlean/2024/projects/clawcode/.awos/worktrees/rs_<id>
git diff
python3 -m pytest tests/ -q
```

Merge manually — auto-merge is off by default.
