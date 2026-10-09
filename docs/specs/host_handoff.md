# M1 host: job hand-off (`scaffold/agent/host/handoff.py`)

Stage 1 (one excellent worker): an unattended job must end in something a
human can review in the morning. That is a local branch plus a short report.
It is never a push and never a change to the user's checkout.

## API

```python
finish(job: dict, workspace: str, outcome: dict, *, job_dir: str | None = None) -> dict
```

- `job`: a JobSpec as a dict (`id`, `goal`, `repo_path`, `budget_usd`,
  `created_at`, ...). It may also carry `base_commit`.
- `workspace`: the directory where the worker made its edits. This can be a
  git worktree of the target repo or a plain copy.
- `outcome`: what the runner reports. All keys are optional: `base_commit`,
  `tests` (`{"status","passed","failed","command"}`), `cost_usd`, `minutes`,
  `summary`, `status`.
- `job_dir`: defaults to `$AWOS_HOST_DIR/jobs/<id>/`. When `AWOS_HOST_DIR` is
  not set, the root is `~/.awos/host`.

`finish` returns, and also writes to `handoff.json`:

```
{job_id, status, branch, base_commit, commit, files, excluded,
 report_path, handoff_path, summary, warnings, secret_scan}
```

`status` is one of:

| Status | Meaning |
|--------|---------|
| `handed_off` | The branch was created or already matched. |
| `no_changes` | The workspace had no diff, so no branch was made. |
| `blocked_secret` | The diff contained a credential, so no branch was made. |
| `error` | Bad base commit, branch conflict, or not a git repo. |

## How the branch is built

`finish` uses git plumbing only. It never touches the user's HEAD, index or
working tree:

1. Resolve the base commit from `job.base_commit`, then `outcome.base_commit`,
   then the repo's `HEAD`. When it falls back to `HEAD`, it adds a warning.
2. Set `GIT_INDEX_FILE` to a temporary file and run
   `git --git-dir=<repo> --work-tree=<workspace>`:
   - `read-tree <base>`
   - `add -A`
   - `rm --cached` on the excluded paths
   - `write-tree`

   The workspace's `.gitignore` applies.
3. If the tree equals the base tree, return `no_changes`.
4. Scan the added lines of `diff-tree -p base new` for secrets. If one is
   found, return `blocked_secret` and create no ref. The loose objects that
   were written stay unreferenced, and `git gc` removes them.
5. Run `commit-tree` with a fixed AWOS identity, so no user git config is
   needed.
6. Run `update-ref refs/heads/awos/<id8> <commit> ""`. The empty
   old-value makes this create-only.

**Idempotency.** If `awos/<id8>` already exists with the same tree and parent,
it is reused. This covers a re-run after a crash. If it exists with a
different tree, `finish` refuses with `error`. It never force-overwrites a
branch a human may have touched.

**Paths that are always excluded:** `.env`, `.env.*`, `.awos/`, `*.pem`,
`*.key`, `id_rsa*`. They are listed under `excluded` in the report.

**Never pushes.** The module contains no network git operation. A test checks
that a configured remote is left untouched.

## Uncommitted changes in the user's checkout: warn, don't refuse

Building the branch never reads or writes the user's index or working tree,
so a dirty checkout cannot corrupt the hand-off, and the hand-off cannot
corrupt the checkout. Refusing would throw away a night of paid work over
something the human can sort out in seconds.

Instead, `finish` compares the dirty files in the checkout
(`git status --porcelain`) with the handed-off files. Any overlap goes in the
report as a merge-conflict warning.

## Credential guard

Two scans run: one over the diff's added lines and one over the rendered
report. They look for:

- **Known token shapes:** `sk-…` (OpenAI, Anthropic, OpenRouter),
  `ghp_`/`github_pat_`, `AKIA…`, `xox[abpr]-`, `AIza…`, and
  `-----BEGIN … PRIVATE KEY-----`.
- **Literal environment values:** the values of variables whose names
  contain `KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `PASSWD` or `CREDENTIAL`,
  where the value is at least 8 characters.

A hit in the diff blocks the branch. A hit in the report text is redacted to
`[REDACTED:<pattern>]` before the report is written, and is recorded as a
warning. Findings name only the pattern or the variable name, never the
value.

## Report (`report.md`)

The report contains:

- the goal, verbatim, in a fenced block;
- the status and branch;
- the base commit;
- what changed (`diff --stat`, file list, excluded files);
- test status;
- cost against budget;
- minutes;
- warnings;
- how to review: `git diff <base-branch>...awos/<id8>`, `git log`, and
  `git branch -D` to discard it.

## Live proof

`python -m scaffold.agent.host.handoff --demo` builds a temp repo and a fake
workspace change, then calls `finish`. It prints the branch, its diff and the
report, and checks that the user's checkout is unchanged.
