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

## Live proof

- `[PLANNER] N tasks touch F file(s) → running as one task` appears in the run
  logs.
- The stop-after-green trace line appears in the run logs.
- The per-job turns in the results JSON.
