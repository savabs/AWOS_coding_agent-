---
description: "Write a session checkpoint — capture what happened, decisions made, and exact next steps. Run before ending any session."
mode: agent
tools:
  - read_file
  - create_file
  - run_in_terminal
  - list_dir
  - file_search
---

# Session Checkpoint

Write a checkpoint file before ending the session. This is the handoff artifact — the next session
must be able to cold-start from this file alone.

## Step 1: Gather State

Read (in parallel):
- The active task file(s) in `tasks/active/`
- The last 5 git commits: `git log --oneline -5`
- The test suite result: `python -m pytest --tb=no -q 2>&1 | tail -5`
- Any error messages from the current session

## Step 2: Determine Filename

Format: `docs/memory/checkpoint_<YYYY-MM-DD>_<slug>.md`

Where `<slug>` is a 2–3 word description of the session's main focus (e.g., `kalman-fusion`, `web-search-tool`, `debug-hmm`).

## Step 3: Write the Checkpoint

```yaml
---
title: "Checkpoint: <YYYY-MM-DD> — <one-line summary>"
tags:
  - doc/checkpoint
  - phase/<N>
  - topic/<slug>
date: <YYYY-MM-DD>
---
```

### What Happened
- What was attempted (brief)
- What succeeded — include evidence: test names, file paths, commit hashes
- What failed — include the exact error and what was tried

### Decisions Made
- Any architecture or design decisions (with reasoning)
- Any spec changes or scope changes
- Any approach that was abandoned and why

### Current State
Task: `tasks/active/<name>.md`

Steps completed this session:
- [x] Step N.N: <description>

Steps remaining:
- [ ] Step N.N: <description>

### Blocked Items
- <what is blocked> — <why> — <what would unblock it>
- (or: "none")

### Environment Notes
- Any environment setup needed for next session
- Any credentials or env vars that must be set
- Any background processes that must be running

### Next Session Start
1. Run `python scripts/session_warmup.py`
2. Read this checkpoint
3. Open `tasks/active/<name>.md`
4. Execute step <N.N>: <exact description>

### Related
- `[[<task>]]`
- `[[<spec>]]`
- `[[<research>]]`

## Step 4: Post-Write Checks

After writing the checkpoint:
```bash
# Verify checkpoint was created
ls docs/memory/ | grep checkpoint | tail -3

# Rotate if too many
python scripts/rotate_checkpoints.py --keep 15 --dry-run
```

If dry-run shows >30 checkpoints, run without `--dry-run`.

## Step 5: Confirm

Output:
```
CHECKPOINT WRITTEN: docs/memory/checkpoint_<date>_<slug>.md
Next session: open that file first, then run session-start prompt.
Safe to end session.
```
