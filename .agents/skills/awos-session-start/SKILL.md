---
name: awos-session-start
title: "AWOS session start"
tags:
  - doc/skill
  - topic/awos-workflow
description: Use at the beginning of an AWOS coding session or when context is unclear; load canonical project state, active tasks, and the latest checkpoint before planning or editing.
---

# AWOS session start

Use this skill before answering a new development request in this repository.

1. Run `python3 scripts/session_warmup.py` from the repository root.
2. Treat warmup output as a hint, not the sole source of truth. If its checkpoint ordering is stale, identify the latest dated file under `docs/memory/` manually; the newest comprehensive checkpoint may be newer than warmup reports.
3. Read `AGENT_INDEX.md`, `memories/repo/project_structure.md`, the latest checkpoint, and relevant `tasks/active/` files. Follow wiki links only as needed.
4. Check `git status --short` before making changes so existing user work is not overwritten.
5. Declare a compact session state: project, active task, last checkpoint, next action, and available validation tools.
6. Do not claim a task is complete from the conversation history alone. Confirm it from files and commands.

Canonical ownership matters: project facts belong in `memories/repo/project_structure.md`, task progress belongs in its task file, and historical session facts belong in an immutable checkpoint. Do not duplicate changing facts across those files.
