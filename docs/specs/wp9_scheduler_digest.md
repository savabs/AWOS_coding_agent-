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

## Acting on the digest (`scheduler/actions.py`)

Tests: `tests/test_digest_actions.py`. The digest page stays offline and script-free. Every item
shows its `id` (also the HTML `id` anchor, e.g. `approval-<id>`, `archived-<run>:<msg>`) and a
copyable CLI command. Next to the page, `actions.todo` (and `actions-<date>.todo`) holds the same
commands, commented out. The owner uncomments lines and runs `apply`. Nothing posts anywhere.

| action | what it does | learning |
|---|---|---|
| `approve <id>` / `--all` | carries out a queued archive through the mailbox adapter, then reads it back | implicit evidence "this scope is junk" (candidate, `model_agreed` = true because the triage proposed it) |
| `deny <id>` | drops a queued archive; writes nothing | implicit evidence for the exclude "never junk" (candidate, no model agreement) |
| `undo <run-id>:<msg-id>` (or a bare msg id), `undo --run <run-id>` | moves archived mail (auto or approved) back to INBOX, read back | user rule `min_importance: 1` for the scope |
| `correct <msg-id> --importance N [--category C] [--needs-reply y/n] [--note ..] [--job J]` | no mail write | user rule with the given labels for the scope |
| `review [--json]` | lists the review queue with a model-free suggestion (`keyword_predict`), known rules for the sender, and a `correct` command | none |
| `apply <file>` | runs each uncommented line; a pasted full command line is accepted | as per line |

Guarantees:
- **Idempotent.** Action ids are `approve:<approval id>`, `deny:<approval id>`,
  `undo:<run>:<msg>` and `correct:<msg>:<hash of values+note>`.
  - `<home>/actions/ledger.jsonl` records `intent` before a mail write, `learned` after the prefs
    update, and `done` last.
  - A repeated action returns `already_done` and changes nothing.
- **Crash-safe.** If an `intent` has no `done` (killed between the write and the ledger), the next
  attempt reads the message back and returns `reconciled`. The write is never repeated. An undo of
  mail the owner already moved back is also `reconciled`, with no write.
- **Write policy.** A mail write needs `approvals.writes` ≠ `deny` **and** a writable adapter.
  Otherwise the result is `refused`, with the reason, and the action can be retried.
  - Actions open the adapter writable for `queue` and `auto_bulk`: an owner approval is the gate
    `queue` waits for.
  - The overnight run itself stays read-only under `queue`.
- **Audit.** One redacted line per attempt goes to `<home>/actions/audit.jsonl`. Ledger entries and
  notes written into prefs are redacted too (SecretBroker).
- **Scope.** From `correction_capture.scope_for`: the sender address for people, address + subject
  pattern for services. Never domain-wide. User rules outrank every learned rule
  (`prefs.precedence`) and decide at L1 when they fix importance and category.
- **Digest.** The next digest has a "Learned from your corrections" section: rules touched by
  actions since the last digest, with source and status. Approved archives join "Archived" with
  their undo. Undone archives are marked "moved back". Corrected review items leave the queue.
- **Exit codes.** `0` when every result is `done`, `already_done` or `reconciled`. `1` for
  `refused`, `failed` or `not_found`. `2` for bad arguments.

```
S="python -m scaffold.agent.desktop.scheduler --home ~/.awos/scheduler"
$S review
$S approve 'inbox@20261019T0600Z:archive:m498dbf'      # or: $S approve --all
$S deny    'inbox@20261019T0600Z:archive:m77aa01'
$S undo    'inbox@20261019T0600Z:m498dbf'              # or: $S undo --run inbox@20261019T0600Z
$S correct m12c0de --importance 3 --category urgent_action --needs-reply y --note "my accountant"
$EDITOR ~/.awos/scheduler/digest/actions.todo && $S apply ~/.awos/scheduler/digest/actions.todo
```

Limits:
- A `fake:` mailbox keeps its state in memory, so from the CLI a mail write lasts only for that
  process. The CLI says so. The ledger, audit and learned rules are saved. Tests share one
  FakeMailbox through `Actions(adapter_factory=...)`.
- Implicit candidates promote only with model agreement. Denials carry none, so a deny-exclude
  never promotes on its own; it becomes active only through `promote(model_check=...)` or an
  explicit `correct`.
- `approve` of a non-archive approval (a skill's `needs_user`) only records the approval and
  prints the `run <job>` command. The runner does not read it yet.

## CLI

```
python -m scaffold.agent.desktop.scheduler [--jobs F] [--home D] list
python -m scaffold.agent.desktop.scheduler run <job> [--force]
python -m scaffold.agent.desktop.scheduler run-due
python -m scaffold.agent.desktop.scheduler digest [--notify] [--out DIR]
python -m scaffold.agent.desktop.scheduler plist [--out FILE] [--python PATH]
python -m scaffold.agent.desktop.scheduler status
python -m scaffold.agent.desktop.scheduler approve <id> | --all
python -m scaffold.agent.desktop.scheduler deny <id>
python -m scaffold.agent.desktop.scheduler undo <run-id>:<msg-id> | --run <run-id>
python -m scaffold.agent.desktop.scheduler correct <msg-id> --importance N [--category C] [--needs-reply y/n] [--note ".."]
python -m scaffold.agent.desktop.scheduler review [--json]
python -m scaffold.agent.desktop.scheduler apply <file>
```

`plist` writes `com.awos.scheduler.plist`. Its contents:
- `StartCalendarInterval` for every calendar job and the digest, plus `StartInterval` for
  `every` jobs
- `RunAtLoad` false, `ProcessType` Background, `LowPriorityIO`, `Nice` 10

It prints the `launchctl bootstrap` / `bootout` commands for the owner. It never installs anything.

## Not done here (follow-ups)

- **Applying approvals and undo.** Done (see "Acting on the digest"). A local page with buttons
  was deliberately not built: it would need a listening server and scripts, and the
  copy-a-command / `actions.todo` flow keeps the page offline.
- **Display gate for UI jobs.** The lock-screen probe (`ioreg` `CGSSessionScreenIsLocked`) is
  best effort. An unknown state is refused (fail closed). It should be verified with a
  `probes.py` lock-screen probe on the real Mac.
- **IMAP over an overnight run.** IMAP is wired through `mail_imap.ImapGmail` but not exercised
  here (no real account); credential errors become `NEEDS_USER`.
