---
auto_execution_mode: 0
description: AWOS session cold-start — load project state before any user request
---
Before responding to ANY user request in this AWOS project, complete this cold-start ritual:

**Step 1 — Run the warmup script:**
```bash
python scripts/session_warmup.py
```
This prints: latest checkpoint, active tasks with step counts, last commits, project snapshot.
If the script doesn't exist, continue to Step 2 manually.

**Step 2 — Load project state (if warmup script unavailable):**
1. Read `AGENT_INDEX.md` — agent cold-start map with file directory and artifact creation rules
2. Read `memories/repo/project_structure.md` — canonical project facts
3. List `docs/memory/` → read the most recent checkpoint file
4. List `tasks/active/` → read every active task file

**Step 3 — Declare operational state** before answering:
```
=== SESSION STATE ===
Project: <name>
Active task: <file> — Step <N>: <description>
Last checkpoint: <date> — <one-line summary>
Next action: <specific next step>
=====================
```

**Never skip the cold-start.** A session that skips state-loading diverges from reality within 2–3 turns.
