# Spec — host job worktree lifecycle (D17)

**Source:** `docs/research/rea_synthesis_2026-10-16.md` item 17, modelled on the
Codex desktop app (`docs/research/rea_digest_2026-10-16.md`, Codex section).
**Code:** `scaffold/agent/host/workspace.py` (worktrees),
`scaffold/agent/app/gitops.py` (merge-back). Hooks in `host/worker.py`:
`prepare_workspace` claims, `remove_workspace` snapshots first, `_finish` calls
`retire_workspace`.

## Owner record

Every job worktree `<job_dir>/workspace` has `<job_dir>/workspace.owner.json`:
`job_id`, `repo_path`, `path`, `base_commit`, `owner_pid`, `owner_started`
(`ps lstart`, so a reused pid is not mistaken for the owner), `owner_host`,
`created_at`, `last_used_at` / `last_used_ts`, `state` (`active` | `finished`),
`released_at`. `prepare_workspace` writes it (a retry re-claims it for the
current worker); `retire` marks it `finished` when the job is final.

## Cap

`AWOS_MAX_WORKTREES` (default 20) job worktrees are kept per storage root.
When a job finishes, its baseline worktree is removed, its workspace is kept
for inspection and `prune_worktrees` removes the least recently used worktrees
beyond the cap. A worktree is never pruned while:

- the queue says its job is not final (queued, running, paused, blocked);
- the job is unknown to the queue and the record is not `finished`;
- the record is `active` and its owner is alive, or cannot be checked
  (another host, or no start time recorded).

Live worktrees stay even when that leaves more than N. A worktree made before
owner records uses its mtime as last use. `AWOS_MAX_WORKTREES=0` removes each
workspace as soon as its job is final (the old behaviour, plus a snapshot).

## Snapshot before delete

Every removal (prune, `remove_workspace`, baseline drop) first commits the
worktree's state to `refs/awos/snapshots/<job>/<UTC stamp>` (`-2`, `-3`… on
a clash; refs are create-only). The commit's parent is the worktree's HEAD
(so commits made on the detached HEAD are kept) and its tree is HEAD plus staged,
unstaged and untracked files, built in a copy of the worktree's index: the
worktree's index, HEAD and files are not changed. `.gitignore` applies;
`__pycache__`, `*.pyc` and `.pytest_cache` are always left out. A clean worktree
still at its base commit gets no snapshot. If the snapshot fails the worktree is
kept and the error is reported. `list_snapshots(repo, job)` and
`restore_snapshot(repo, ref, dest)` (a new detached worktree) read them back.
Snapshot refs are never deleted automatically.

## Transactional merge-back

The only step that applies a job's result to the user's checkout is
`gitops.merge_branch` (the app's `m` / `/merge`; hand-off itself only creates
the `awos/<id>` branch). It runs as a transaction:

1. **Plan** with `git merge-tree --write-tree` (git ≥ 2.38; writes objects
   only): `applied` (paths the merge changes in the user's branch), `skipped`
   (paths the branch changes that the user's branch already has as-is) and
   `conflicts`. Any conflict: refused, nothing touched, paths reported.
2. Refused when uncommitted changes touch an `applied` path (dirty `skipped`
   paths are fine).
3. `git merge --no-ff`; on failure abort and check HEAD is back (else
   `git reset --merge` to the old HEAD).
4. `on_merged(head)` (the app records the review there); if it raises, the merge
   is undone with `git reset --merge`, which keeps unrelated local edits.

`MergeResult` carries `applied`, `skipped`, `conflicts` and `rolled_back`.
Older git without `merge-tree --write-tree` skips step 1 (the previous
behaviour) and reports what the merge commit changed.
