# WP9: local scheduler + morning digest

Product #2 (private Mac agent), BLUEPRINT §4 WP9 and Critic item 1. The code is in
`scaffold/agent/desktop/scheduler/` and the tests are in `tests/test_scheduler_digest.py`.

## What it does

The Mac runs owner-defined jobs unattended: overnight email triage, taught skills and micro-tasks.
Each morning it writes a private digest page of what happened and what needs the owner.

```
launchd (com.awos.scheduler, calendar fires; a missed fire is delivered once on wake)
  └─ python -m scaffold.agent.desktop.scheduler run-due
       ├─ resume any INTERRUPTED run (checkpoint)
       ├─ for each due job: admission → model lock → llama-server → execute → run record
       └─ at [digest] at: write digest HTML + text (optional notification)
```

## Jobs: `~/.awos/jobs.toml`

The path can be overridden with `--jobs` or `$AWOS_JOBS_FILE`. The full format is in the
docstring of `scheduler/jobs.py`.

| key | values |
|---|---|
| `kind` | `email_triage` \| `skill` \| `micro_task` |
| `schedule` | `daily HH:MM`, `weekdays HH:MM`, `mon,wed,fri HH:MM`, `every N[m\|h]` (minimum 5 min) |
| `mailbox` | `fake:<testbed mailbox dir>` \| `imap:<gmail user>` (Keychain app password, via `mail_imap.ImapGmail`) |
| `agent` | `keyword_prefs`: scripted L3 with no model; mail it is unsure of goes to the review queue. `local_model`: llama-server L3. |
| `constraints` | `ac_power`, `idle` (seconds, or `true` = 300), `max_runtime`, `quiet_hours`, `unlocked` (defaults to true for skill/micro_task) |
| `approvals.writes` | `queue` (default), `auto_bulk`, `deny` |
| `command` | skill/micro_task only: an argv list (no shell). The last JSON line printed to stdout gives the outcome. |

Due times:
- Schedules use wall-clock time in `[scheduler] tz`, which defaults to the Mac's zone.
- **DST:**
  - A wall time that does not exist (spring forward) runs at the first instant after the gap.
  - An ambiguous wall time (fall back) runs once, at its first occurrence.
  - `every N` slots are fixed UTC intervals.
- **Missed runs:** only the most recent missed slot runs ("catch up once"), and `missed_slots`
  records how many were skipped. A slot older than `catchup_hours` (default 24) is skipped.
- A new job never fires for slots before it was first seen.

## Runner guarantees (`scheduler/runner.py`)

- **Idempotent per run id.** The run id is `<job>@<slot UTC>`.
  - A run id that already has a `result.json` is never run again (the outcome is `DUPLICATE`).
  - A run id with a checkpoint resumes from it.
- **Admission.** Checks, in order:
  1. quiet hours
  2. AC power (`pmset`)
  3. user idle time (`ioreg` HIDIdleTime)
  4. unlocked screen, for UI jobs (Critic 1: synthetic input does not reach a locked session)
  5. for model jobs: no live `local_runtime` task lease held by another task
  6. the llama-server memory plan (`local_runtime.plan`)

  A refusal is recorded as `DEFERRED` with a reason. The slot stays due.
- **One model job at a time.** This is a non-blocking `flock` on `<home>/model.lock`, released by
  the kernel if the process dies.
- **llama-server.**
  - The runner starts it with `scripts/local_model.sh start` only when it is down, and stops it
    afterwards only if this run started it.
  - While the job runs, a task lease is held, so the idle watchdog never unloads the model mid-job.
- **Checkpoint/resume.**
  - Email triage checkpoints after every chunk of messages.
  - SIGTERM and `max_runtime` stop the run at the next chunk boundary as `INTERRUPTED`, and the
    next `run-due` resumes it first.
  - `max_runtime` uses the monotonic clock, which stops while the Mac sleeps.
  - Writes are logged as *intent* before they are made and *done* after. On resume, an intent
    without a done is reconciled by reading the message back; the write is never repeated blindly.
- **Writes.**
  - `queue`: nothing is written, and every proposed archive goes to `approvals.jsonl` for the digest.
  - `auto_bulk`: only mail that L1 or L2 put in newsletter/promo/social is archived. Protected,
    suspicious, phishing and review-queue mail is never archived, and everything else is queued.
  - Every applied write is read back. `VERIFIED` requires all of them to have landed; otherwise
    the outcome is `RECONCILIATION_REQUIRED`.
- **Outcomes:** `VERIFIED`, `COMPLETED_UNVERIFIED`, `HALTED`, `NEEDS_USER`,
  `RECONCILIATION_REQUIRED`, `INTERRUPTED`, `DEFERRED`, `FAILED`, `DUPLICATE`.
- **Run records.**
  - `<home>/runs.jsonl` has one line per attempt.
  - `<home>/runs/<run_id>/` holds `result.json`, `checkpoint.json`, `actions.jsonl` (the undo
    ledger), `triage.json` and `log.txt`.
  - Everything is passed through `SecretBroker.redact` (WP7) before it is written.

## Digest (`scheduler/digest.py`)

The digest is written to `<home>/digest/digest-<date>.html` and `.txt`, plus `latest.html` and
`latest.txt`. Files are mode 0600. The digest covers runs since the last digest, with a 36 h
window on the first one.

Sections:
- urgent mail, with why: guard, rule or model
- needs a reply
- review queue: the cascade abstained, so the mail stayed in the inbox
- archived, with undo for each (move back to INBOX)
- flagged injection and phishing
- approvals waiting
- skills that halted (one-step fix demo)
- learned-rule changes since the last digest, plus the full `render_learned`
- every run's outcome, including deferred runs and why

Privacy:
- The page has no scripts and no tags that load anything (img/link/iframe/...).
- No `src`/`href` attributes.
- CSP `default-src 'none'`, `referrer no-referrer`.
- All strings are HTML-escaped after redaction.
- It works offline.
- `--notify` (or `[digest] notify = true`) runs `osascript display notification` with counts
  only, and never during the digest's quiet hours.

## CLI

```
python -m scaffold.agent.desktop.scheduler [--jobs F] [--home D] list
python -m scaffold.agent.desktop.scheduler run <job> [--force]
python -m scaffold.agent.desktop.scheduler run-due
python -m scaffold.agent.desktop.scheduler digest [--notify] [--out DIR]
python -m scaffold.agent.desktop.scheduler plist [--out FILE] [--python PATH]
python -m scaffold.agent.desktop.scheduler status
```

`plist` writes `com.awos.scheduler.plist`. Its contents:
- `StartCalendarInterval` for every calendar job and the digest, plus `StartInterval` for
  `every` jobs
- `RunAtLoad` false, `ProcessType` Background, `LowPriorityIO`, `Nice` 10

It prints the `launchctl bootstrap` / `bootout` commands for the owner. It never installs anything.

## Not done here (follow-ups)

- **Applying approvals and undo.** Approving a queued item and per-item "back to inbox + learn"
  from the digest page need a small local approve/undo command or page. The digest feeding WP2
  corrections, from the BLUEPRINT acceptance, is also still to do.
- **Display gate for UI jobs.** The lock-screen probe (`ioreg` `CGSSessionScreenIsLocked`) is
  best effort. An unknown state is refused (fail closed). It should be verified with a
  `probes.py` lock-screen probe on the real Mac.
- **IMAP over an overnight run.** IMAP is wired through `mail_imap.ImapGmail` but not exercised
  here (no real account); credential errors become `NEEDS_USER`.
