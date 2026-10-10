# Spec: M1 host ops — always-on setup, watchdog, morning report

Status: implemented (ops piece of M1 host). VISION stage 1 — one excellent
worker that keeps working unattended. No model or API calls anywhere in ops.

## Why

The long runs this week died for reasons that had nothing to do with the model:

| Cause | What happened | Detection | Action |
|---|---|---|---|
| Laptop sleep / crash / hang | worker silently stopped; nobody noticed until morning | `<root>/heartbeat` (JSON `{ts,pid,note}`) older than `--max-age` (default 300 s), or missing/corrupt | report STALE + `launchctl kickstart -k gui/$UID/ai.awos.host`; launchd `KeepAlive` restarts after a crash; worker re-queues "running" jobs from the journal |
| Revoked / invalid key | every job failed with 401 and burned its retries | `classify_backend_error` → `auth` (HTTP 401/403, "error code: 401", "invalid api key", "user not found", …) | write `<root>/PAUSED` (`kind=auth`), append to `notifications.jsonl` (+ macOS banner when `AWOS_HOST_NOTIFY=osascript`); resume with `watchdog resume` |
| Out of credit | same, 402 | `billing` class | same as auth (`kind=billing`) |
| DNS / network outage | jobs failed instead of waiting | DNS resolve of the backend host fails, or error class `network` (gaierror, "nodename nor servname", "Connection error", timeouts) | exponential backoff 30 s → 30 min cap in `<root>/network_backoff.json`; `network_ready()` false inside the window; never pauses; reset on first success |
| Battery | the Mac slept or died off AC | `pmset -g batt` → source + percent | on battery: WARN; ≤ 15 %: pause (`kind=battery`) + notify |

Key material is redacted (`sk-xxxxxx…`, `Bearer …`, JWTs, `AIza…`, `hf_…`,
`gh?_…`, `gsk_…`/`xai-…`, `api_key=…`/`token=…`) before anything is written to
`PAUSED` or notifications.

### Review fixes (2026-10-10)

* **Pauses accumulate.** `pause_queue` never overwrites an earlier reason. The
  most severe cause (auth/billing > manual > battery; ties keep the first) is the
  primary `kind`/`reason`; every distinct kind is kept in `PAUSED.reasons`, shown
  by `check`, the morning report and `resume`. A repeated battery check neither
  re-pauses nor re-notifies.
* **Moderation 403 is not auth.** A 403 (or any error) whose text mentions
  moderation / flagged / content policy is class `content`: a job-level failure,
  no pause. A bare 403 without such text is still `auth`.
* **Morning report time filter** parses `created_at` stored as epoch seconds or
  ms (number or numeric text) as well as ISO, and sorts chronologically.
* **Backoff ownership.** `network_backoff.json` records `source` (`api` from the
  worker's real connect/timeout failures, `dns` from the watchdog probe). The
  watchdog's DNS success only clears a `dns` backoff. The read-modify-write is
  under an `fcntl` lock shared by watchdog and worker.
* **Notification dedupe.** `check --notify` notifies once per distinct set of
  failing `(check, status)` and repeats it at most every 6 h
  (`NOTIFY_REPEAT_S`); state in `<root>/notify_state.json`, cleared on an
  all-ok run or `resume`.
* **Installer** refuses `--apply` while `scaffold/agent/host/worker` does not
  exist (KeepAlive would restart a failing process every 30 s forever), unless
  `--allow-missing-worker` is given with `AWOS_HOST_CMD` set in the env file.
* `pmset_advice.sh` prints a caffeinate line that handles a non-running worker,
  and states that `pmset restoredefaults` is global (no per-source variant).
* `run_host.sh` tries GNU `stat -c` before BSD `stat -f` (GNU `-f` exits 0).

## Contract with the other M1 pieces

* Root: `AWOS_HOST_DIR`, default `~/.awos/host/`.
* The **worker** (queue builder) should: call `watchdog.write_heartbeat(note=...)`
  every loop tick and between job steps; skip starting jobs while
  `watchdog.is_paused()` is truthy or `watchdog.network_ready()` is false; pass
  backend exceptions to `watchdog.handle_backend_error(err, job_id=...)`, and
  call `watchdog.record_network_ok()` (no `source`) after a successful API call.
  Entry point expected by launchd: `python -m scaffold.agent.host.worker`
  (override with `AWOS_HOST_CMD`).
* The **morning report** reads `queue.sqlite` read-only (`mode=ro`). The jobs
  table is found by PRAGMA (a table with a `goal` column, preferring `jobs`);
  `state`/`status`, `result` JSON (`cost_usd`, `duration_s`|`minutes`,
  `branch`, `tests`), `started_at`/`finished_at` are all optional. Per-job
  `handoff.json` (branch, summary, report) and `report.md` override/fill in.

## Files

| Path | Role |
|---|---|
| `scaffold/agent/host/watchdog.py` | heartbeat, error classification, pause flag, notify, network backoff, pmset parsing; CLI `check | beat | classify [--apply] | resume | status` |
| `scaffold/agent/host/morning_report.py` | `<root>/morning_report.md`: totals ($, minutes, branches), per-job table (goal, state, $ (budget), min, branch, tests), needs-attention, review list, host health |
| `scripts/host/install_launchd.py` | generates `ai.awos.host` (KeepAlive `SuccessfulExit=false`, RunAtLoad, ThrottleInterval 30, logs under `<root>/logs`) and `ai.awos.host.watchdog` (every 300 s) plists into `<root>/launchd/`, prints `launchctl bootstrap` commands. Writes to `~/Library/LaunchAgents` / runs launchctl **only** with `--apply`. `--uninstall` prints `bootout` commands. |
| `scripts/host/run_host.sh` | what launchd runs: sources the env file (refuses unless mode 600/400), execs the worker. Secrets live only in the env file, never in the plist. |
| `scripts/host/pmset_advice.sh` | prints current power state and recommended `pmset -c` (AC-only) settings; applies nothing. |

## Usage

```
python scripts/host/install_launchd.py            # dry run: stage plists, print commands
bash scripts/host/pmset_advice.sh                 # print recommended power settings
python -m scaffold.agent.host.watchdog check      # exit 1 if stale / paused / backoff
python -m scaffold.agent.host.morning_report      # write <root>/morning_report.md
```

## Not done here (gaps)

* The worker's honouring of `PAUSED`/`network_ready()` and heartbeat writes is
  the queue builder's piece; until wired, the watchdog detects but cannot stop jobs.
* Notifications are file + optional macOS banner; no phone push.
* The battery check pauses but does not auto-resume when AC returns.
