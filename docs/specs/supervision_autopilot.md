# Supervision autopilot

**Why:** about 29% of what the owner types to AI agents is supervision:
"continue/resume/go ahead" (16%), "what's the status?" (7%) and fixing API keys
(6%, e.g. revoked OpenRouter keys). An agent OS should not need that babysitting.
This spec covers the four host features that take those prompts over.

Code: `scaffold/agent/key_health.py`, `scaffold/agent/notify.py`,
`scaffold/agent/host/autopilot.py`, wired into `scaffold/agent/host/worker.py`
and `awos.py`. Tests: `tests/test_key_health.py`, `tests/test_notify.py`,
`tests/test_host_autopilot.py`.

## 1. Key health: `awos keys`

Each provider gets one free, read-only request. Key values are never printed,
logged or returned.

| provider   | request                                             | checked when |
|------------|-----------------------------------------------------|--------------|
| anthropic  | `GET {ANTHROPIC_BASE_URL}/v1/models?limit=1`        | key set |
| local      | `GET {AWOS_LOCAL_BASE_URL minus /v1}/health`        | always (not-configured if nothing answers and no URL is set) |
| openrouter | `GET https://openrouter.ai/api/v1/key`              | only when `OPENROUTER_API_KEY` is set |

Each result is one of: `ok` (2xx/3xx, or 429, which still means the key was accepted),
`unauthorized` (401/403), `unreachable` (no answer, timeout, other 4xx/5xx) or
`not-configured`. The env checked is the env a job's child process would see: the
process env first, then the repo `.env` fills in only the names that are missing.
`awos keys` exits 1 when there is a credentials problem.

**Blocking rule** (`credentials_problem`): a provider the job is allowed to use
returns `unauthorized`, or a `cloud_ok` job has no configured provider at all.
A `local_only` job checks only the local server. An outage is not a
credentials block.

**In the worker:**
- *Before each job* (turn off with `AWOS_HOST_KEY_CHECK=0`): if there is a
  problem, the job goes to `blocked` with error `blocked: credentials (<provider>
  <status>)`, and `<job_dir>/blocked.md` names the one action to take.
- *After an auth error*: when the failed attempt's error or the child's log
  section matches `AUTH_ERROR_RE` (`invalid x-api-key`, `authentication_error`,
  `Error code: 401`, OpenRouter's `User not found`, ...), the worker runs the
  check again for a precise action and blocks the job. It does not fail the job.
- `blocked` is a new non-final state. The worker never claims a blocked job,
  so there is no retry loop. On each loop pass (at most once every 60 s) the
  worker checks the keys again. Once they pass, it re-queues every
  credentials-blocked job and refunds the attempt that was blocked. It then
  notifies `unblocked`. `awos unblock <id>` does the same by hand, and
  `awos cancel` works on blocked jobs.
- One notification per credentials problem: the dedupe key is
  `blocked|credentials|<provider>|<status>`, so many parked jobs produce one
  notification. When the block clears, that key is forgotten, so the next
  problem notifies again.

## 2. Notify (`notify.py`)

Every final state (`done`/`failed`/`cancelled`), plus `blocked`, `continued`
and `unblocked`, produces one event: a one-line summary (state, goal, tests,
cost, branch or error) and the report path.

- (a) `<AWOS_HOST_DIR>/inbox.jsonl`: written for every event, append-only, fsynced.
- (b) On darwin, unless `AWOS_NOTIFY_MACOS=0`, the worker also runs
  `osascript -e 'display notification "<msg>" with title "<title>"'`. Quotes
  and backslashes are escaped, newlines are collapsed, and the call has a 5 s
  timeout. A failure never raises. Every command is logged to
  `<AWOS_HOST_DIR>/notify.log`.
- Secrets are scrubbed from all text: secret-shaped strings (`sk-ant-`,
  `sk-or-`, `ghp_`, bearer tokens), plus the value of any `*_API_KEY`,
  `*_TOKEN`, `*_SECRET` or `*PASSWORD` env var.
- Dedupe: an identical event (same key) inside `AWOS_NOTIFY_DEDUPE_SEC`
  (default 6 h) is dropped. State is kept in `notify_state.json` behind a file lock.

## 3. Digest: `awos status --digest`

The digest shows:
- the running job, its elapsed time and its last journal step;
- the queued count;
- jobs finished since the last digest (state, host test result, cost, review branch);
- blocked jobs, each with the one action the owner must take;
- spend today, for host jobs and for all AWOS according to the ledger.

`awos status --digest` moves the "since" marker forward
(`digest_state.json`). After every final or blocked state, the worker writes
the same digest to `<AWOS_HOST_DIR>/digest.md` without moving the marker.

## 4. Auto-continue

When an attempt fails, the worker sorts the stop into one of three kinds:
`credentials` (handled in section 1), `limit` or `hard`. A limit stop matches
`max_turns`, `ran out of turns`, `wall_budget`, `timed out after`,
`repeated_tool_call` or `truncated`. The worker queues **one** continuation
only when all of these hold:

- the stop is a limit stop;
- continuations used < `AWOS_HOST_AUTOCONTINUE` (default 1);
- the job's budget is not used up (`spend.json`);
- the job has an isolated workspace;
- there is evidence of progress: the workspace diff against the base commit
  (tracked changes and untracked files) is non-empty and changed during the
  attempt, or the host's `verify_tests` passed count rose compared with the
  previous attempt.

A continuation re-queues the job in the same workspace (the seq is kept, so it
runs next). The child's goal gets this note appended:
`NOTE: continue from where you stopped: <stop reason>; files changed so far: ...;
last said: <agent's last message>. The workspace already holds your earlier edits; do not start over.`
The worker emits a `continued` event. If any condition fails, the job fails
with the reason added, for example `(no auto-continue: no progress: no change
in the workspace)`, and `result.autocontinue` records the decision.

**Deferred rollback.** On total failure the orchestrator restores its file
snapshots, and it reverts a failed task's files. Without a fix, the edits of a
stopped attempt would be gone before the worker could see them. While a
continuation is still available, the worker sets
`AWOS_DEFER_ROLLBACK=<job_dir>/deferred_rollback.jsonl` for the child.
`git_manager` then records those restores instead of applying them. The worker
deletes the record when it continues, and applies it otherwise. That covers
success, hard failure, credentials blocks and the last allowed attempt, so the
final result matches the behaviour from before the autopilot. When the record
changes the workspace, the worker re-runs the host test evidence.

Trade-off: inside one deferred attempt, a later task sees the files a failed
earlier task left behind instead of reverted ones. Most host jobs run as a
single task, because the planner skips small goals.

## Also fixed (found by the live proof)

`SpendTracker` counted ledger entries from an index taken when the attempt
started. At month rollover, `BudgetLedger` drops last month's entries, so the
index pointed past the end and the job was billed $0 (live: $0.0010 spent,
$0 reported). Entries are now counted by the child's pid wherever they sit.
The runner reports `max(child cost, host per-pid cost)`.

## Known limits

- An attempt the host kills on timeout can lose its unrecorded LLM spend: the
  ledger records calls as they finish, and an in-flight call is lost.
- When the repo has tests, the LLM goal check is skipped (391d3bb). A
  multi-part goal can then come back `done` after only part of the work,
  because the existing tests still pass. Auto-continue does not trigger then,
  since the attempt reports success.
