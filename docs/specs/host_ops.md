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

Key material is redacted (`sk-xxxxxx…`) before anything is written to
`PAUSED` or notifications.

## Contract with the other M1 pieces

* Root: `AWOS_HOST_DIR`, default `~/.awos/host/`.
* The **worker** (queue builder) should: call `watchdog.write_heartbeat(note=...)`
  every loop tick and between job steps; skip starting jobs while
  `watchdog.is_paused()` is truthy or `watchdog.network_ready()` is false; pass
  backend exceptions to `watchdog.handle_backend_error(err, job_id=...)`.
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
