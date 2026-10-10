# Spec — M1 host: step journal + checkpoint/resume

Stage: VISION Stage 1 (one excellent worker) — reliability of a long job that
survives crashes, sleep, and restarts without paying twice for work already done.

Owner file: `scaffold/agent/host/journal.py` · tests: `tests/test_host_journal.py`

## Problem

A host job (see the M1 host contract) runs for minutes to hours. If the worker
process dies (SIGKILL, OOM, reboot, laptop lid), today the job starts over: every
LLM call is paid again and every side effect is re-applied blindly. We need an
append-only record of what a job did so a restarted worker can skip finished work,
re-check side effects against the real world, and continue from the right point.

## Storage

`<AWOS_HOST_DIR or ~/.awos/host>/jobs/<job-id>/journal.jsonl` — one JSON object
per line, opened in append mode, each write followed by `flush` + `os.fsync`
(the directory is fsynced once when the file is created).

Record shape (`v` = 1):

```json
{"v":1,"seq":7,"ts":"…","key":"tool:write:a.py","kind":"tool_call",
 "status":"started|done|failed","payload":{…},"result":{…}}
```

## Step kinds

| kind | meaning | on replay |
|------|---------|-----------|
| `llm_call` | a model reply; `result` holds the reply (cassette `_reply_to_dict` shape) keyed by the cassette conversation fingerprint | **skip**, serve cached reply — never re-billed |
| `tool_call` | an action with side effects (write file, run command) | **re-verify** via a verifier callback; rerun only if the effect is missing |
| `checkpoint` | a resume marker carrying a workspace fingerprint | skip; used to detect workspace drift |
| any other | pure/bookkeeping step | skip when `done` |

## API

- `Journal(job_dir, fsync=True)`
- `append(step) -> dict` — `step` needs `kind`; `key` is the idempotency key
  (derived from `kind`+`payload` hash when absent). Appending a record whose
  `(key, status)` already exists is a no-op that returns the existing record.
- `steps() -> list[dict]` — latest record per key, in first-seen order.
- `records() -> list[dict]` — raw valid records.
- `last_checkpoint() -> dict | None`
- `replay_plan(verifier=None, workspace=None) -> ReplayPlan`
  - `done`: keys that are finished and need no check (llm/checkpoint/other)
  - `to_verify`: side-effect keys (done or in-doubt `started`) to re-verify
  - with `verifier(step) -> bool`: `verified` (effect present → skip) and
    `rerun` (effect absent → run again); in-doubt steps the verifier confirms
    are recorded as `done` so the check is not repeated
  - `incomplete`: `started`/`failed` non-side-effect steps (to rerun)
  - `resume_after`: key of the last step that may be skipped
  - `last_checkpoint`, `workspace_drift` (current fingerprint ≠ checkpoint's)
- `run(key, kind, fn, verify=None, payload=None)` — exactly-once helper:
  returns the cached result if done (re-verifying side effects), otherwise
  writes `started`, calls `fn()`, writes `done` with the result (or `failed`).
- `checkpoint(key, workspace)` — records `workspace_fingerprint(workspace)`.
- `JournalingClient(inner, journal, root=None)` — wraps any agent-loop
  ModelClient; reuses `cassette._fingerprint` / `_reply_to_dict` /
  `_reply_from_dict` so a resumed run is served journaled replies for free.
- `workspace_fingerprint(path)` — sha256 over (relative path, size, content
  hash) of every file, skipping `.git`; plus git `HEAD` when available.

## Corruption tolerance

A crash mid-write leaves a truncated last line. On open, if the file does not end
in `\n`, the partial tail is cut back to the last newline (repair) so new appends
do not glue onto garbage. Unparseable lines anywhere are skipped and counted in
`Journal.corrupt_lines`; reading never raises on bad data.

## Non-goals

No multi-job coordination (queue builder), no handoff, no network. Behaviour of
existing commands is unchanged: nothing imports this module unless the host
worker does.

## Live proof

`python -m scaffold.agent.host.journal demo` runs a scripted 6-step job
(llm, tool, checkpoint, tool, llm, tool) in a child process that SIGKILLs itself
after step 4, then resumes in a fresh child. Expected markers: run 1 exit -9;
run 2 shows steps 1-4 `skip`/`verified`, 5-6 `executed`; the effect log shows
every side effect exactly once and the fake model called exactly twice in total.
