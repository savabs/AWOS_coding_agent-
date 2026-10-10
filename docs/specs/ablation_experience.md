# Ablation B: learning from its own past fixes (experience store)

Pre-registered 2026-10-08, before any run. Stage 1 (one excellent worker), and
the moat test: *gets better at your work*.

## Why

The moat the owner chose is "private on your box + gets better at your work".
The project notebook, which holds free-text notes, has not shown a benefit
beyond noise. What a developer learns from working on a codebase is concrete:

- which files a kind of change touches;
- the project's idioms;
- how a similar bug was fixed last time.

AWOS already produces verified fixes. Keeping them and showing the most
similar ones on the next task is the cheapest form of compounding. It needs
no training. It works with any model, consistent with "models are
replaceable, intelligence compounds". It stays on the box.

## Change (one change: `AWOS_EXPERIENCE`, default 0)

1. **Store.** When a task ends with visible tests green and success, append
   one record to `.awos/experience/<repo_key>.jsonl`. `repo_key` is a stable
   key for the project; for benchmark jobs it is the series name, so all jobs
   of one series share a store. The record holds the goal text, the files
   changed, the final diff (trimmed), the test command and status, the cost
   and the turns.

   Only verified-green results are stored. Failed or rolled-back attempts are
   never stored.
2. **Retrieve.** At the start of a task, when `AWOS_EXPERIENCE=1`, take the
   top 3 past records from the same `repo_key` by lexical similarity of goal
   text and file names (BM25-style; no embeddings needed). Prior job state
   must not leak beyond these records.
3. **Use.** Add them to the one-shot context and to the agent's first message
   as "Past verified changes in this repo (for reference)": goal, files,
   trimmed diff, capped at about 3k tokens in total.
4. **Training-data log (always on, not part of the comparison).** Every
   finished task appends its full trajectory (the call log for the task, the
   final diff and the verdict) to `.awos/trajectories/<date>.jsonl`. This is
   the dataset for a later fine-tuning experiment. It is not used at
   inference.

For the benchmark, the store must not carry over between repeats or arms. The
runner gives each (arm, repeat) its own fresh experience store, so learning
happens only within one pass over the series. That is the compounding
question: does job 7 benefit from jobs 1–6?

## Design

- **Series:** **backupd**, the 12-job held-out series in one project
  (`tests/job_series/backupd`), run in order. Its jobs build on the same
  codebase, so later jobs can benefit from earlier verified fixes.
- **Arms:** AWOS `off` (notebook off), `AWOS_EXPERIENCE=0` versus
  `AWOS_EXPERIENCE=1`. Same code and current defaults. `--repeat 2`.
- **Primary metric:** solve rate over the 12 jobs, paired by job.
- **Secondary metrics:**
  - billed $/job and turns/job, which is where compounding should show up
    first;
  - the same split for jobs 1–6 versus 7–12, where the effect should grow
    with the store;
  - the store size at each job.

## Decision rule (written before running)

**Adopt (default 1)** when **either** of these holds:

- solve rate up with at most 1 job worse;
- solve rate not down (within one job) **and** billed $/job down at least 20%
  on jobs 7–12;

and also, in both cases:

- billed $/job over all 12 jobs rises at most 30%;
- no job drops from 2/2 to 0/2 because of misleading retrieved changes (check
  the logs for any 2→0 job).

**Reject (default 0)** otherwise. The training-data log stays on either way.

## Amendment before running (2026-10-08)

Ablation C2 was adopted after this spec was written, so `AWOS_ACCEPTANCE` now
defaults to 2, and ablation A may also be adopted before this runs. Both arms
run on the defaults current at launch, and the launch records them in each
run's inputs. Everything else is unchanged. Recorded before any ablation B
run.

## Result (2026-10-08): REJECTED (default stays 0), with a promising signal

The 4 backupd passes ran in the order 0, 1, 0, 1 from 18:40 to about 22:15.
The C2 gate was on in both arms. One run was invalid and retried
automatically. Report: `docs/memory/runs/report_ablationB_experience/report.md`.

| Job | Control (exp 0) | Experience (exp 1) |
|---|---|---|
| 1 | 2/2 | 2/2 |
| 2 | 1/2 | **2/2** |
| 3 | 2/2 | 2/2 |
| 4 | 1/2 | **2/2** |
| 5 | 0/2 | **2/2** |
| 6 | 0/2 | 0/2 |
| 7 | 0/2 | **1/2** |
| 8 | 2/2 | 2/2 |
| 9 | 0/2 | **1/2** |
| 10 | 0/2 | 0/2 |
| 11 | 0/2 | **1/2** |
| 12 | 2/2 | **0/2** |
| **Total** | **10/24** | **15/24** |

**Decision checks:**

- **Solve rate:** +21 pp per job, CI [−8, +46], sign-flip p = 0.28, with
  6 jobs better and 1 worse. This check passes.
- **Cost:** billed **+50%** (logged +49%) over all 12 jobs, against a +30%
  limit. This check fails.
- **Collapse:** **job 12 went from 2/2 to 0/2**, very likely misled by
  retrieved changes. This check fails.

The change is rejected. `AWOS_EXPERIENCE` stays 0. The trajectory log stays
on, as pre-registered.

**Why job 12 failed.** A record is saved on visible-green success, which is
the only signal available at run time. Visible-green fixes can still be
wrong. By job 12 the store held 11 records (jobs 1–11), about 4 of which
fail their hidden tests.

Job 12 ("prune with exactly the same rules as automatic pruning, keep_last
from the settings file") received 3 records (about 2.7k tokens). In both
passes it failed exactly `test_keep_last_from_ini`, `test_prune` and
`test_pruning_is_logged`. So the store can spread mistakes as well as
knowledge.

**Confound.** The jobs B improved most (J2, J4, J5) are the ones hurt by the
planner single-task rewrite bug found during this run, and by variance. Past
records from the same project may restate what the goal really wanted. The
goal-preservation fix (`6e585bb`) may capture part of this gain by itself.

**A revised B (B2) would need these changes,** run as its own pre-registered
run on top of the goal fix:

1. Store only records whose acceptance check (the C2 gate) was active and
   passed. That is a stronger trust signal than visible-green alone.
2. Inject at most 2 records and about 1.5k tokens, to bring the cost down.
3. Label the injected records "may contain mistakes; the goal and tests win".
