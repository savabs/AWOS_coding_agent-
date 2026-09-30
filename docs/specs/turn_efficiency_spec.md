# Turn efficiency — fixes 1 and 2

Stage 1 (one excellent worker). Objective: useful work ÷ dollar. This advances
**planning** and **execution**: the same model does the same work in fewer
turns.

## Why

Head-to-head on ordertool, 2026-09-29, all arms on DeepSeek V4 Flash
(`.awos/job_series_20260929T131340.json`):

| Arm | Solved | Billed per job | Solved per $1 |
|---|---|---|---|
| AWOS, notebook off | 6/12 | $0.032 | ~15 |
| AWOS, notebook on | 7/12 | $0.038 | ~16 |
| Aider | 5/12 | $0.011 | ~38 |

The run's logs contain 842 AWOS turns across 24 jobs. They break down as
follows:

- **215 turns (25%)** were spent on later planned tasks and restarts that found
  the work already done. Single-task jobs averaged 20 turns; multi-task jobs
  averaged 51.
- **220 turns (25%)** came after the tests were green following the last edit.
  Most of them were hand-written CLI checks.
- The rest went to re-reading files (about 20%), exploring before the first
  edit (15%), and failed edits and scripts (8%). Those are fixes 3 and 4, and
  are not part of this spec.

## Fix 1: one task for small goals, no redundant work (`orchestrator.py`)

- **Collapse small plans.** When a plan has more than one task and the tasks
  name at most `AWOS_SINGLE_TASK_MAX_FILES` distinct files (default 4; 0 turns
  collapsing off), run the goal as one task. The planner's tasks become a
  numbered checklist in that task.
- **Chain context.** An agent-loop task passes the files it changed and its
  summary to the next task. That task is told to confirm with `run_tests` and
  finish if the work is already done.
- **No nudge when earlier tasks changed files.** Do not push for an edit when
  an earlier task in the goal already changed files. The judge's "already
  satisfied" check verifies it with the tests.
- **No restart when green.** An attempt that stopped on `max_turns` or
  `repeated_tool_call` is a success, not restarted, if it changed files and the
  project's tests all pass.

## Fix 2: stop rule (`agent_loop.py`, `cheap_planner.py`)

- **System prompt.** Once the tests pass and the requirements are met, finish.
  No ad-hoc scripts for behaviour the tests cover; at most one manual check.
- **Green reminder.** A passing `run_tests` after an edit carries the note
  "finish now".
- **Hard cap.** After `AWOS_AGENT_POST_GREEN_TURNS` turns (default 6; 0 turns
  it off) with no change since the last green run, the loop ends as completed.
  The orchestrator re-runs the tests independently.
- **Planner.** Tasks name a test or an observable behaviour as their check, not
  a manual command.

## Success criteria (pre-registered)

Re-run the ordertool series with arms `off,on` on the same pinned model,
compared with run `20260929T131340`:

1. Mean turns per job drop by at least 40% in both arms.
2. Mean billed cost per job drops by at least 30%.
3. Jobs solved do not drop by more than 1 per arm (6/12 off, 7/12 on). With 12
   jobs that is within noise, so a larger drop means the stop rule cuts real
   work.

If criteria 1 and 2 hold and criterion 3 holds, the fixes stay. If criterion 3
fails, find which cap cut real work, using the `post_green_stop` and collapse
markers in the logs.

## Result: run 20260930T125415 (ordertool, off and on arms, DeepSeek V4 Flash)

| Criterion | Off | On |
|---|---|---|
| 1. Turns per job fall ≥40% | 33.7 → 18.4 (−45%) ✅ | 34.1 → 16.2 (−53%) ✅ |
| 2. Billed cost per job falls ≥30% | $0.032 → $0.024 (−26%) ❌ | $0.038 → $0.022 (−41%) ✅ |
| 3. Solved falls by at most 1 | 6 → 8/12 ✅ | 7 → 8/12 ✅ |

Five of the six checks pass. The miss is not in the agent's own spend.

- **Where the off arm's cost went.** Agent spend (ledger) fell 46%, from $0.0315 to $0.0171 per job. The primary planner, DeepSeek V4 Flash, returned empty JSON on 6 of 12 jobs, against 3 of 12 before. Each empty reply costs a call and 2–3 minutes before the fallback runs.
- **Planner overhead.** The planner cost is now a large share of the bill. It is the next fix: skip the primary planner for small goals, or put a cheaper, non-reasoning planner first.

The stop rule costs some real work.

- **Where it lost jobs.** Two lost jobs (on j03, both arms' j07) ended right after the first green test run with one hidden edge case missing.
- **Adjustment.** When the tests are green, have the agent run each untested clause of the goal once (failure paths, exact messages) before it finishes.
- **Plan collapse.** In on j02 the collapsed checklist carried the planner's guessed design into the task. Label the steps as suggestions and treat the goal as the requirement.

Against Aider in the 2026-09-29 run:

- **Solved per $1 billed.** AWOS is now at about 28 (off) and about 30 (on), up from about 15–16. Aider was at about 38, solving 5 of 12.
- **Solved jobs.** AWOS solves more jobs, 8 of 12 against Aider's 5.

## Live proof

- `[PLANNER] N tasks touch F file(s) → running as one task` appears in the run
  logs.
- The stop-after-green trace line appears in the run logs.
- The per-job turns in the results JSON.
