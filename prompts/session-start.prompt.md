---
description: "Cold-start a new session — load project state, active tasks, and declare operational readiness before doing anything else."
mode: agent
tools:
  - file_search
  - list_dir
  - read_file
  - run_in_terminal
  - memory
  - tool_search
---

# Session Start — Cold-Start Ritual

Execute the following steps IN ORDER. Do not skip any step. Do not answer the user's question until this ritual is complete.

## Step 1: Run Warmup Script

```bash
python scripts/session_warmup.py
```

If the script doesn't exist or fails, continue to Step 2 manually.

## Step 2: Discover Available Tools

Call `tool_search` with the query `"file read write search terminal web git memory github"` to load the full deferred tool set. Note which MCP servers responded.

## Step 3: Load Project State

Execute in parallel:
1. Read `memories/repo/project_structure.md` — canonical project facts, metrics, roadmap
2. List `docs/memory/` → identify the most recent checkpoint file → read it
3. List `tasks/active/` → read ALL active task files

## Step 4: Identify Current Position

From the files read above, extract:
- Which phase/task is currently active
- Which step within that task is next
- What was last completed (from checkpoint)
- What is blocked (if anything)

## Step 5: Declare Operational State

Output this status block verbatim (fill in the blanks):

```
=== SESSION STATE ===
Project: <name from project_structure.md>
Phase:   <current phase>
Task:    <active task file> — Step <N>: <step description>
Last CP: <checkpoint date> — <one-line summary>
Next:    <exact next action — file to edit, command to run, etc.>
Blocked: <anything blocked, or "none">
Tools:   file_io ✓ | terminal ✓ | fetch_webpage ✓ | tavily <✓/✗> | git <✓/✗> | github <✓/✗> | context7 <✓/✗>
=====================
```

## Step 6: Ask

"Ready. What would you like to work on, or shall I proceed with the next step?"

Do NOT proceed with any implementation until the user confirms or redirects.
