# Spec — M1 host job queue + worker + CLI

**Milestone:** M1 of `docs/product/agent_computer.md` (always-on host).
**Research:** `docs/research/local_first_architecture_2026-10.md` §2.1 (always-on host, step journal).
**VISION stage:** 1 (one excellent worker) — reliability of execution, not new capability.

## Goal

A job handed to the host is durable from the moment it is submitted, runs on a
single long-lived worker, and survives the worker being killed at any point:
on the next start every job reaches a final state.

## What exists and what is reused

`scaffold/agent/job_host.py` already runs jobs from JSON files under
`.awos/jobs/` (`awos submit --root`, `awos host`). This package keeps that path
untouched and adds the shared M1 host contract on top of it:

| Reused from job_host.py | Used for |
|---|---|
| child-process run (`job_host.py _child`, ABILITIES, `_run_coding`) | the default runner: the orchestrator entry point used by `awos run` |
| `process_start` / `process_matches` / `_kill_group` | killing an orphaned child only if its pid still names it |
| flock single-host lock | one worker per storage root |

New: SQLite queue (atomic transitions), storage root shared with the journal,
handoff and ops builders, budget + privacy per job, a pluggable runner with a
step/stop context.

## Storage

- Root: `AWOS_HOST_DIR`, default `~/.awos/host/`.
- `<root>/queue.sqlite` — table `jobs`, WAL mode.
- `<root>/jobs/<id>/worker.log` — worker lines + child output.
- `<root>/jobs/<id>/journal.jsonl`, `report.md`, `handoff.json` — owned by the
  journal / handoff builders; the worker only calls their APIs.
- `<root>/worker.lock` — flock, released by the OS even on `kill -9`.

## JobSpec (`scaffold/agent/host/models.py`)

`id` (uuid4 hex), `goal` (verbatim, never stripped or rewritten), `repo_path`,
`kind` ("coding"), `budget_usd`, `privacy` ("local_only" | "cloud_ok"),
`created_at` (ISO), `state` (queued|running|paused|done|failed|cancelled),
`attempts`, `result` (dict|None). Optional bookkeeping: `started_at`,
`finished_at`, `error`, `cancel_requested`, `worker_pid`, `child_pid` (+ start
times).

## Queue (`scaffold/agent/host/queue.py`)

- Every transition is one `BEGIN IMMEDIATE` transaction with a
  `WHERE state IN (...)` guard; it returns whether it happened. Two processes can
  never both claim a job or both finish it.
- `submit`, `get`, `list`, `claim_next` (oldest queued → running, attempts+1),
  `finish(id, state, result, error)`, `requeue`, `request_cancel`, `recover`.
- `cancel`: queued/paused → cancelled at once; running → `cancel_requested=1`,
  the worker stops the job at its next step boundary and marks it cancelled.
- `recover()` (worker start, lock held so no other worker is alive): every
  `running` job is orphaned. Kill its child if `process_matches` says it is
  still that process; then re-queue it, or fail it when
  `attempts >= AWOS_HOST_MAX_ATTEMPTS` (default 3).

## Worker (`scaffold/agent/host/worker.py`)

Loop: recover → claim → run via runner → handoff → finish. Single worker.

- Runner signature: `runner(job: JobSpec, ctx: RunContext) -> dict`. The dict
  has at least `success`. `ctx` offers `step(name, **data)` (journals a step),
  `should_stop()`, `on_child(pid)`, `log(msg)`, `job_dir`, `replay` (the
  journal's `replay_plan()` on a resumed attempt, else None).
- Default runner (`subprocess_runner`): writes a job_host-compatible Job file in
  the job dir and runs `job_host.py _child` (the orchestrator, planner path).
  `budget_usd` → `AWOS_MAX_RUN_COST`. `privacy=local_only` blanks the cloud API
  keys in the child env (fail closed: cloud calls cannot authenticate).
- `sleep_runner`: demo/test runner, `AWOS_HOST_FAKE_STEPS` steps of
  `AWOS_HOST_FAKE_STEP_SEC` seconds; skips steps the journal says are done.
- Journal: `from .journal import Journal` if importable, else a no-op shim.
  Journal errors never fail a job.
- Handoff: `from .handoff import finish` if importable, else a minimal
  `report.md`. Handoff errors are logged, the job's outcome stands.
- SIGTERM/SIGINT: stop flag. The in-process runner finishes its current step,
  then the job goes back to `queued` (no attempt is burned by a clean stop).
  The subprocess runner forwards SIGTERM to the child group and waits up to
  `AWOS_HOST_STOP_GRACE_SEC` (default 30) before SIGKILL. A second signal stops at once.
- Writes `<root>/heartbeat` (unix time) each loop for the ops watchdog.

## CLI (additive; old behaviour unchanged when the new flags are unused)

| Command | Behaviour |
|---|---|
| `awos submit "<goal>" --repo PATH [--budget USD] [--privacy local_only\|cloud_ok]` | queue in the SQLite host queue; prints the id. Without `--repo` the old `.awos/jobs` path runs as before. |
| `awos serve --worker [--once] [--poll S] [--runner mod:func]` | run the host worker. Without `--worker`, `serve` still starts the web dashboard. |
| `awos status [id]` | table of jobs, or one job as JSON + its paths |
| `awos cancel <id>` | cancel (queued now; running at next step) |

`AWOS_HOST_RUNNER=module:callable` also selects the runner.

## Tests (`tests/test_host_queue.py`)

Fake runners only, no model calls: submit round-trip (goal verbatim), atomic
claim (one winner), transition guards, cancel queued/running, recover
re-queues / fails after max attempts / kills a live orphan child, SIGTERM-style
stop re-queues without burning an attempt, journal + handoff shims, local_only
env scrubbing, CLI parse + end-to-end via subprocess.

## Live proof

`scripts/live_proof_host_queue.sh`: sleep runner, 3 jobs, `awos serve --worker`
in the background, SIGKILL mid-job, restart, all 3 reach `done`; status printed
before and after.

## Not in M1

Multiple workers, priorities, `paused` set by a user command, a journal-driven
step replay for the orchestrator itself (the orchestrator has no step hook
yet; a resumed coding job restarts from the start in its workspace).
