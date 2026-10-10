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
   - `read-tree <base>`, then `ls-files --cached --others --exclude-standard`
     to list paths (the workspace's `.gitignore` applies);
   - `hash-object -w --no-filters` on every listed file (symlinks are hashed
     as their target string, never followed). There is **no `git add`**, so
     no clean filter (e.g. `filter=lfs`), attribute or hook ever sees
     workspace content, and nothing lands in the user's `.git` (such as
     `.git/lfs/objects`) before the scan;
   - excluded paths are reverted to their base entry (or dropped);
   - a fresh temp index is built with `update-index --index-info`, then
     `write-tree`.

   Nested repositories (untracked dirs with their own `.git`) are skipped
   with a warning; base gitlinks are kept as-is.
3. If the tree equals the base tree, return `no_changes`.
4. Scan (see Credential guard) every changed path's **name** (when new) and
   its **full new blob**, whatever git would call the file. If a secret is
   found, return `blocked_secret` and create no ref.
5. Run `commit-tree` with a fixed AWOS identity, so no user git config is
   needed.
6. Copy into the user's repo only the objects reachable from the new commit
   and not from the base, then run `update-ref refs/heads/awos/<id8> <commit> ""`.
   The empty old-value makes this create-only.

**Object staging.** Steps 2-5 run with `GIT_OBJECT_DIRECTORY` pointed at a
private temp dir and the repo's object store as
`GIT_ALTERNATE_OBJECT_DIRECTORIES`. Excluded files (`.env`, keys) and
blocked secret-bearing content are therefore never written into the user's
`.git/objects`, not even as unreferenced loose blobs. The temp dir is removed
when `finish` returns.

**Hardened git environment.** Every git call scrubs `GIT_CONFIG_PARAMETERS`,
`GIT_CONFIG_COUNT/KEY_*/VALUE_*`, `GIT_EXTERNAL_DIFF` and `GIT_ATTR_SOURCE`
from the caller's env, sets `GIT_ATTR_NOSYSTEM=1`, and forces (via
`GIT_CONFIG_*`, which beats repo/global config) `core.bigFileThreshold=1024g`,
`core.attributesFile=/dev/null`, `core.hooksPath=/dev/null`,
`core.fsmonitor=false`, `core.splitIndex=false`, `core.untrackedCache=false`.
`diff-tree --stat` runs with `--no-ext-diff --no-textconv`; git output is
decoded with `errors="replace"`, and any unexpected exception still produces
`status: error` plus a report. The dirty-checkout check uses
`git --no-optional-locks status`.

**Job id.** `job.id` must match `[A-Za-z0-9_-]+`, be at most 128 characters,
and contain a character other than `-` (so `awos/<id8>` is never `awos/`);
anything else raises `HandoffError` before any file or object is written,
because the id forms both a path (`jobs/<id>/`) and a ref name.

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

Two scans run: one over the change (new path names plus full new blobs) and
one over the rendered report. They look for:

- **Known token shapes:** `sk-…` (OpenAI, Anthropic, OpenRouter),
  `ghp_`/`github_pat_`, `AKIA…`, `xox[abpr]-`, `AIza…`, and
  `-----BEGIN … PRIVATE KEY-----`.
- **Literal environment values:** the values of variables whose names
  contain `KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `PASSWD` or `CREDENTIAL`,
  where the value is at least 8 characters. Matched against the whole text,
  so multi-line values are found.

Blob scanning: patterns run on a UTF-8 view, UTF-16 views at both byte
offsets and UTF-32 views at all four (non-ASCII code units become spaces);
env values are matched as their encoded bytes in UTF-8, UTF-16 LE/BE and
UTF-32 LE/BE, so non-ASCII values match too. This runs on **every** changed
blob, not only ones git's 8000-byte heuristic calls binary. A content finding
counts only if the new blob has more occurrences than the base blob at the
same path, so already-committed text never blocks.

A hit in the change blocks the branch. The report is **always** redacted
(env values longest-first, so overlapping values leave no suffix, then
patterns) before it is written; a report hit is also recorded as a warning. Findings name only the pattern or the variable name, never the
value. `handoff.json` is redacted field by field before serialising, so a
secret containing `"` or `\` is still caught (redacting the JSON text would
miss its escaped form).

Renames in the dirty-checkout check: `status --porcelain -z` emits the
original path of a rename/copy as its own unprefixed entry; both paths count
as dirty.

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
