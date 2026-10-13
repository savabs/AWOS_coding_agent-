# AWOS interactive terminal app (`awos`)

The goal: AWOS should look and feel like Claude Code from the outside, while
the background engine underneath stays the same (queue, worker, private
worktree, host-verified tests, review branch).

Before this, one task took four commands (`awos submit`, `awos serve --worker`,
`awos status`, `git diff`). Now it takes one:

```
cd my-repo
awos                      # interactive session
awos "fix the failing test"   # one-shot
```

Code: `scaffold/agent/app/`. CLI wiring: `awos.py` `main()`.
Tests: `tests/test_app_*.py`.

## What the user gets

1. **Session.** Running `awos` with no arguments inside a git repo prints a
   header with the repo and branch, the detected test command, the model,
   and the queue state. It then shows a prompt. Each line typed in plain
   English becomes a host job for this repo (`JobQueue.submit`), and several
   jobs can wait in the queue. Outside a git repo, `awos` prints the guide as
   it did before, plus a tip.
2. **The worker starts automatically.** If no worker serves `AWOS_HOST_DIR`,
   the app starts `awos serve --worker` in its own session (`setsid`). The
   worker survives closing the terminal, and its output goes to
   `<host>/worker_daemon.log`. Two workers never run at once (see Guard
   below).
3. **Live progress.** For each running job the app tails `worker.log` and
   `journal.jsonl` and turns the raw lines into one-line updates, for example
   "reading calc.py", "writing acceptance tests (2 kept)", "trying a one-shot
   fix", or "running tests… 2 passed". These lines print above the prompt,
   so you can keep typing while a job runs. When a job finishes, the app
   prints a card with the outcome (done, failed or blocked), tests, cost,
   time and branch.
4. **Inline review.** Each review command acts on the latest finished job.
   You can add a job id to pick a different one.
   - `d`: show the diff (coloured, paged through `less -R -F -X` when it is
     long).
   - `m`: merge `awos/<id>` into your current branch with
     `git merge --no-ff`. The merge is refused when your uncommitted changes
     touch files the job changed; the app names those files. If git reports
     a conflict, the merge is aborted, so your tree is never left half-merged.
   - `r`: reject. The app asks y/N, then deletes the branch.
   - `f`: follow-up. It queues a continuation job that starts from the
     previous job's branch (see below).
   - `o`: show the report.
5. **Reopen.** Running `awos` again lists the active jobs and recent results
   for this repo, including jobs that finished while the app was closed. A
   job is tagged `[needs review]` until you merge or reject it. The app
   records that choice in `<job_dir>/app_review.json`.
6. **One-shot.** `awos "fix X"` takes one quoted goal that contains a space.
   It submits the goal, starts the worker if needed, and streams progress
   with an animated status line. When the job is done it offers single-key
   review: `[d]iff [m]erge [r]eject [f]ollow-up [o]pen report [q]uit`. A
   single unknown word, such as a mistyped subcommand, never becomes a job.
   argparse reports the error and suggests quoting the goal.
7. **Slash commands.** `/status`, `/diff [id]`, `/merge [id]`, `/reject [id]`,
   `/follow [id] <task>`, `/open [id]`, `/cancel <id>`, `/watch`, `/keys`,
   `/help` and `/quit`. Ctrl-C or Ctrl-D leaves the session and background
   jobs keep running. All existing subcommands (`submit`, `serve`, `status`,
   `keys`, `cancel`, `unblock`, ...) and `awos --help` work as before.

Design rules the code follows:

- **No raw tracebacks.** An error shows one line and the path to
  `<host>/app.log`, where the full trace is written.
- **Plain text when not a TTY.** Without a TTY there are no colours, no
  redraws, and piped input is echoed so a transcript reads naturally.
  `NO_COLOR` and `TERM=dumb` are respected.
- **Explicit actions only.** Nothing is merged, deleted or pushed unless you
  ask. Nothing is ever pushed.

## Modules (`scaffold/agent/app/`)

| File | Role |
|------|------|
| `session.py` | `App`: header, monitor thread, cards, review actions, slash commands, `interactive()` / `oneshot()`; `run_session` / `run_oneshot` entry points |
| `progress.py` | `ProgressTranslator` (raw log line -> friendly line or None) and `JobTail` (byte-offset tail of `worker.log` + `journal.jsonl`, partial-line safe, de-duplicated) |
| `workerctl.py` | `worker_running`, `ensure_worker` (spawn guard), detached spawn |
| `gitops.py` | `merge_branch` (dirty-overlap refusal, conflict abort), `delete_branch`, `diff_text`, `dirty_files` |
| `jobs.py` | per-repo job listing, outcome summary, review bookkeeping, `submit_follow_up` |
| `ui.py` | `Term`: colours, status line, printing above a pending prompt, a small line editor, single-key reads, pager |
| `detect.py` | test-command guess for the header (display only) |
| `demo_runner.py` | model-free host runner (scripted edit, real worktree/tests/hand-off) for demos and tests |

**Why there is a custom line editor and no readline.** macOS Python ships
libedit, and libedit's `get_line_buffer()` returns stale text while another
thread prints. Its `redisplay()` output also goes through a separate C stdio
buffer. Progress lines would therefore garble what the user was typing. The
app instead runs a small editor in cbreak mode that owns its own buffer. It
supports backspace, the arrow keys, Home/End, Ctrl-A/E/U/K/W/D, history, and
paste. ISIG stays on, so Ctrl-C still raises `KeyboardInterrupt`. No new
dependencies were needed: `rich` and `prompt_toolkit` are not used.

**Onboarding hook.** At start the app calls
`scaffold.agent.onboarding.ensure_ready(repo, interactive=<tty>)` if that
module can be imported, and skips the call otherwise. The returned
`test_command` and `model` override what the header detects.

## Worker auto-start guard

The worker holds an exclusive `flock` on `<host>/worker.lock` and writes its
pid into the file once it has the lock.

- **`worker_running`.** If the file names a live pid whose command looks like
  `serve`/`--worker`, a worker is running. Otherwise the app probes the lock
  with a non-blocking flock. The pid check comes first because a probe that
  briefly holds the lock could make a worker that is starting at that instant
  give up. The command check guards against pid reuse.
- **`ensure_worker`.** If a worker is running, it returns `running`.
  Otherwise it takes `<host>/app_spawn.lock`, which serialises app windows,
  checks again, spawns one worker, and then waits for that worker's pid to
  appear. It returns `started`, `spawned` (still starting) or `failed` (the
  worker exited at once, for example because no provider is configured; the
  app shows the daemon log path and the no-provider hint).
- **Even if a spawn race slipped through,** the second worker would fail to
  take `worker.lock` and exit. Two workers never serve one queue.

## Follow-up jobs: the host extension

Before this change, the host always created a job's worktree at the repo HEAD.
The smallest safe extension adds an optional file, `<job_dir>/base_ref`:

- `jobs.submit_follow_up` builds the `JobSpec`. It writes `base_ref`
  (`awos/<prev8>`) and `follow_up_of` **before** `JobQueue.add`, so the
  worker can never claim the job without them.
- `host.worker.prepare_workspace` reads `base_ref`. It accepts only
  `[A-Za-z0-9_./-]+` with no leading `-`, resolves the ref with
  `rev-parse --verify <ref>^{commit}`, and uses that commit as the base. If
  the branch is gone, the job fails with "the branch this follow-up starts
  from (...) no longer exists".
- The hand-off then builds `awos/<new8>` on top of the previous job's commit.
  Merging the follow-up branch therefore brings in both jobs. The goal text
  tells the agent that the earlier changes are already present.

## Test hooks

- `AWOS_HOST_RUNNER=scaffold.agent.app.demo_runner:run` plus
  `AWOS_APP_DEMO_FIX='{"file":..., "old":..., "new":...}'` runs a free,
  model-free job end to end.
- `AWOS_APP_WORKER_ARGS` appends flags to the spawned `awos serve --worker`
  (for example `--poll 0.5`). `AWOS_APP_NO_WORKER=1` never spawns a worker.
  `AWOS_APP_POLL` sets the monitor interval. `AWOS_APP_BUDGET` sets the
  per-job USD cap (default 1.0).
- `tests/test_app_e2e.py` drives the real `awos` through a pseudo-terminal.
  It uses a tiny pexpect-like helper and gives the child a controlling TTY,
  so Ctrl-C delivers SIGINT. A small screen renderer interprets `\r`,
  erase-line and backspace. The tests cover:
  - a session: task, progress, done, diff, merge;
  - typing while progress lines print;
  - Ctrl-C that leaves the job running, followed by reopen, then reject;
  - a one-shot run with open and merge;
  - a follow-up job based on the previous branch;
  - plain mode;
  - `--help`, existing subcommands, and a typo that does not become a job.
- `AWOS_APP_LIVE=1 python tests/test_app_e2e.py [transcript.txt]` runs the
  same session flow and a one-shot flow against the real model.

## Live proof (2026-10-13, claude-haiku-5-5)

The scratch repo `shop/` contains `calc.py` with
`average = sum(xs) / (len(xs) + 1)` and two failing tests. The script is
`AWOS_APP_LIVE=1 AWOS_APP_BUDGET=0.15 python tests/test_app_e2e.py`. It
drives the real app through a pty, and the text below is the rendered
screen, unedited apart from shortened temp paths and the second diff body.
Job costs were $0.0015 and $0.0036. An earlier identical run cost $0.0054,
so live testing spent $0.0105 in total.

```
$ awos
AWOS  ·  /private/var/folders/…/awos-app-live-yntqu2wv/shop (main)  ·  tests: python -m pytest  ·  model: claude-haiku-5-5
queue: 0 running, 0 queued  ·  worker started
  No jobs for this repo yet.
Type a task in plain English. /help for commands; Ctrl-C leaves (background jobs keep running).
awos › fix average() in calc.py so the tests in test_calc.py pass
  + queued 826bb90c (starting now)
  ▶ 826bb90c started: fix average() in calc.py so the tests in test_calc.py pass
    ⠙ 826bb90c  preparing a private copy of the repo at e27ad57c
    ⠹ 826bb90c  scanning the repository
    ⠸ 826bb90c  small goal: running it as one task
    ⠼ 826bb90c  planned 1 task
    ⠴ 826bb90c  task 1: fix average() in calc.py so the tests in test_calc.py pass
    ⠦ 826bb90c  writing acceptance tests (2 kept)
    ⠧ 826bb90c  trying a one-shot fix
    ⠇ 826bb90c  acceptance tests: 2/2 passing
    ⠏ 826bb90c  one-shot fix worked (tests 2 passed)
    ⠋ 826bb90c  task 1 finished
    ⠙ 826bb90c  running tests… 2 passed
    ⠹ 826bb90c  double-checking the goal
    ⠸ 826bb90c  running tests
    ⠼ 826bb90c  reading calc.py
    ⠴ 826bb90c  reading test_calc.py
    ⠦ 826bb90c  searching for 'average'
    ⠧ 826bb90c  checking the result
    ⠇ 826bb90c  goal check: complete
    ⠏ 826bb90c  used: acceptance, goal-check
    ⠋ 826bb90c  verifying: tests 2 passed
  ✓ done  826bb90c  fix average() in calc.py so the tests in test_calc.py p…  [needs review]
      tests 2 passed · $0.0015 · 12s · branch awos/826bb90c
      [d]iff  [m]erge  [r]eject  [f]ollow-up  [o]pen report
awos › d
 calc.py | 2 +-
 1 file changed, 1 insertion(+), 1 deletion(-)
diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
 def average(xs):
-    return sum(xs) / (len(xs) + 1)
+    return sum(xs) / len(xs)
awos › m
  ✓ merged awos/826bb90c into main (a79c38e)
awos › /quit
Bye.

$ awos "add a median(xs) function to calc.py with a test for it"
  + queued 5a3bf86a (starting now)
  ▶ 5a3bf86a started: add a median(xs) function to calc.py with a test for it
    ⠦ 5a3bf86a  preparing a private copy of the repo at a79c38e9
    ⠼ 5a3bf86a  scanning the repository
    ⠴ 5a3bf86a  small goal: running it as one task
    ⠦ 5a3bf86a  planned 1 task
    ⠧ 5a3bf86a  task 1: add a median(xs) function to calc.py with a test for it
    ⠙ 5a3bf86a  writing acceptance tests (3 kept)
    ⠇ 5a3bf86a  trying a one-shot fix
    ⠼ 5a3bf86a  switching to the step-by-step agent
    ⠧ 5a3bf86a  “I'll check the current state of both files before changing anything.”
    ⠇ 5a3bf86a  reading calc.py
    ⠏ 5a3bf86a  reading test_calc.py
    ⠇ 5a3bf86a  “`median` is already in `calc.py`, but `test_calc.py` has no median test, and the earlier …”
    ⠏ 5a3bf86a  editing test_median.py
    ⠏ 5a3bf86a  running tests
    ⠹ 5a3bf86a  “`median(xs)` is in `calc.py` and has tests in a new file, `test_median.py`. The suite pas…”
    ⠦ 5a3bf86a  acceptance tests: 3/3 passing
    ⠧ 5a3bf86a  task 1 finished
    ⠇ 5a3bf86a  running tests… 5 passed
    ⠏ 5a3bf86a  double-checking the goal
    ⠙ 5a3bf86a  reading calc.py
    ⠹ 5a3bf86a  reading test_median.py
    ⠴ 5a3bf86a  running tests
    ⠏ 5a3bf86a  searching for 'average'
    ⠇ 5a3bf86a  checking the result
    ⠏ 5a3bf86a  goal check: complete
    ⠦ 5a3bf86a  used: acceptance, goal-check, notebook
    ⠹ 5a3bf86a  verifying: tests 5 passed
  ✓ done  5a3bf86a  add a median(xs) function to calc.py with a test for it  [needs review]
      tests 5 passed · $0.0036 · 22s · branch awos/5a3bf86a
  [d]iff  [m]erge  [r]eject  [f]ollow-up  [o]pen report  [q]uit › d
 calc.py        |  9 +++++++++
 test_median.py | 13 +++++++++++++
 2 files changed, 22 insertions(+)
 …(full diff of calc.py and test_median.py)…
  [d]iff  [m]erge  [r]eject  [f]ollow-up  [o]pen report  [q]uit › m
  ✓ merged awos/5a3bf86a into main (0bf3dc6)
```

The user's branch afterwards:

```
*   0bf3dc6 Merge awos/5a3bf86a: add a median(xs) function to calc.py with a test for it
|\
| * 434c531 awos: add a median(xs) function to calc.py with a test for it
|/
*   a79c38e Merge awos/826bb90c: fix average() in calc.py so the tests in test_calc.py pass
|\
| * 1a06969 awos: fix average() in calc.py so the tests in test_calc.py pass
|/
* e27ad57 calc with a bug
tests on main: 5 passed in 0.01s
```

## Known gaps

- **Spinner animation.** The spinner animates only in watch mode (one-shot
  and `/watch`). In the session, each progress line carries the next spinner
  frame and the prompt shows `[n working]`.
- **`[o]pen report`** pages the report inside the terminal. It does not open
  an editor or a browser.
- **Terminal testing.** The app was verified through a pseudo-terminal
  (xterm-256color). Nobody has yet run it by hand in Terminal.app or iTerm2.
- **Progress order resolution.** Worker lines carry whole-second stamps, so a
  worker line and a journal step within the same second can still swap.
- **Hung worker.** The periodic check restarts a worker that has died. A
  worker that is alive but stuck (it holds the lock but its heartbeat is
  stale) is only logged, never killed.

## Changelog

### 0.2.1 (2026-10-13)

- **Docs links.** `awos --help`, `awos doctor` (when a check fails), the
  onboarding menu and the no-provider message now link to the site quickstart,
  https://awos-6v8.pages.dev/docs/quickstart/. The old GitHub `docs/INSTALL.md`
  link was dead. The base URL is one constant, `paths.DOCS_BASE_URL`, which
  `AWOS_DOCS_URL` overrides. Switch it to https://awos.tirramind.com once DNS
  is live. Test: `tests/test_docs_links.py`.
- **Progress order.** `JobTail.poll` merges the new `worker.log` and
  `journal.jsonl` lines by timestamp before translating them. Worker lines are
  stamped by their `HH:MM:SS` prefix or the `===== child ... at <iso>` header.
  Journal records are stamped by `ts`. Unstamped child output inherits the
  last stamp in its file. Tests: the interleaved fixtures in
  `tests/test_app_progress.py`.
- **Worker watchdog.** While the session is open and repo jobs are queued or
  running, `App.tick` checks the worker every 15 s
  (`AWOS_APP_WORKER_CHECK_SEC`). If no worker holds `worker.lock`, the app
  restarts one through `workerctl.ensure_worker`, so the existing spawn-lock
  guard still allows only one worker. The app logs the heartbeat age to
  `app.log`. Tests: `tests/test_app_worker_guard.py`, including three windows
  checking at once.
- **One-shot fallback starts clean (engine).** In a host job workspace (the
  worker exports `AWOS_JOB_WORKSPACE`), the files are snapshotted into a git
  tree before the one-shot attempt. A temporary index is used, so HEAD, the
  real index and the files are untouched. If the attempt fails with tests not
  green, the files it changed are restored and the files it created are
  deleted before the agent loop runs. The loop's prompt says the edits were
  reverted. Edits that left the project tests green are kept on purpose
  (cut-off reply, a failed block, an acceptance gap), so the loop continues
  from them. The user's checkout is never touched. `AWOS_ONE_SHOT_RESTORE=0`
  restores the old behaviour. Module: `scaffold/agent/workspace_restore.py`.
  Test: `tests/test_one_shot_restore.py`.
- **Wide characters.** The line editor and the status line measure display
  columns with `unicodedata`: CJK and emoji take 2 columns, combining marks
  and ZWJ take 0. The cursor now stays aligned and scrolling stays correct.
  Test: `tests/test_app_ui_width.py`.
