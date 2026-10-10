# M1 host live proof (2026-10-10)

The `awos serve --worker` host was proven end to end on a real job, using the free local model.

**Setup**
- Model: Qwen3.5-9B Q4_K_M on llama-server (`scripts/local_model.sh start`), `AWOS_PROVIDER=local`.
- Fixture: the buggy `slugify` from `tests/fixtures/host_live_proof/`, copied into a throwaway git repo at `.awos/jobs/host_demo_repo` (ignored path). Two of its three tests fail at the base commit.
- Commands:
  - `awos submit --repo <demo> --privacy local_only --budget 0.5 "<goal>"`
  - `awos serve --worker --once`
  - Host state lived in `AWOS_HOST_DIR` (job tmp).

## Run 1: worked, but exposed 2 bugs the unit tests missed

The job finished in 1.8 min at $0. The handoff made `awos/fe75ad8a`, and its fix passed 3/3. Two bugs showed up:

1. **Worker journal writes failed.** Every step logged `journal append failed: journal step needs a 'kind'`. The worker branch and the journal branch had been built in parallel with different record shapes.
2. **The job edited the user's checkout.** The worker gave the child `root = job.repo_path`. The orchestrator's `GitManager.setup` then:
   - ran `git branch -D awos/<slug>` followed by `git checkout -b awos/<slug>` in the user's repo;
   - left `textutil.py` modified there.

   The handoff then diffed that same checkout, and warned about "uncommitted changes in handed-off files". This breaks the host's core promise: never change the user's checkout.

## Fixes

- **`RunContext.step`** writes `{"kind": "worker_step", "key": <step>, "payload": {...}}`.
- **New `prepare_workspace`**: each job runs in a detached git worktree of the repo at its HEAD, under `<job_dir>/workspace`, and the base commit is recorded in `<job_dir>/base_commit`.
  - A retry reuses the same worktree.
  - The handoff diffs that workspace and receives the base commit.
  - `remove_workspace` drops the worktree once the job is final.
  - A non-git root still runs in place.
- **`AWOS_GIT_BRANCH=0`** (set in the host child env) makes `GitManager.setup` skip its own branching, so a job never creates or deletes refs in the user's repo.
- **Regression tests:** `tests/test_host_workspace.py` (4 tests). The 158 host tests still pass.

## Run 2: clean

Before submitting, the demo checkout was given an uncommitted `NOTES.md` to stand in for the user's in-progress work.

- **Outcome:** done in about 2 min at $0, `status: handed_off`, branch `awos/f21622db`, base `5ca4a32`, commit `570dc1f`, files `[textutil.py]`, **no warnings**.
- **Journal:** 5 `worker_step` records (`attempt_1_start`, `workspace`, `child_started`, `child_exited`, `spend`), with no append errors.
- **The user's checkout was untouched:** still on `main`, `NOTES.md` still the only untracked change, no stray branches, and the job worktree was removed.
- **The handed-off fix** passes 3/3 when run from the branch's files.

## Still open

- The report says "Tests: not reported" and "Cost: not reported". The child result does not yet carry the `tests` and `cost_usd` keys the handoff can render.
- The always-on launchd install (`awos` ops) was generated but has not been applied on this machine.
