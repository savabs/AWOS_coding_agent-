# Spec — `awos worker` CLI

**Research:** `docs/research/awos_worker_cli.md`  
**Task:** `tasks/active/awos_worker_cli.md`

---

## Goal

Simple product surface for coding work: `awos worker start/status/resume/diff/cancel`

Hides kernel jargon (mission, gauntlet, session internals). Thin wrapper over existing orchestrator + sessions.

---

## Commands

### `awos worker start "<goal>"`

**Behavior:**
1. Create new `RuntimeSession` with goal
2. Start orchestrator in foreground
3. Print: `Session: rs_<id>` and `Working in: .awos/worktrees/<id>`
4. Ctrl+C → checkpoint → pause (existing behavior)

**Output:**
```
Session: rs_abc123
Working in: .awos/worktrees/abc123
Main repo unchanged

[TASK 1] Planning...
...
```

**Error cases:**
- Empty goal → "Goal required"
- No API key → (existing orchestrator error)

---

### `awos worker status`

**Behavior:**
1. List all sessions (from `RuntimeSessionStore.list_all()`)
2. Format: one line per session with status, progress, cost
3. If no sessions: "No active work"

**Output:**
```
rs_abc123  running   4/8 tasks   $0.02   "fix auth module"
rs_def456  paused    2/5 tasks   $0.01   "add tests"
```

---

### `awos worker resume [session_id]`

**Behavior:**
1. If no ID: find latest paused session
2. Load session, resume orchestrator
3. Same output as `start`

**Error cases:**
- No paused sessions + no ID → "No paused work. Use 'awos worker start' to begin."
- Session completed → "Session rs_xyz completed. Review with 'awos worker diff rs_xyz'"
- Session cancelled → "Session rs_xyz cancelled"
- Multiple paused + no ID → "Multiple paused sessions. Specify ID: awos worker resume rs_xyz"

---

### `awos worker diff [session_id]`

**Behavior:**
1. If no ID: use latest session
2. Show `git diff main .awos/worktrees/<id>`
3. Print worktree path

**Output:**
```
Sandbox: .awos/worktrees/abc123

diff --git a/src/auth.py b/src/auth.py
...
```

**Error cases:**
- Worktree deleted → "Sandbox missing"
- No sessions → "No work history"

---

### `awos worker cancel [session_id]`

**Behavior:**
1. If no ID: cancel latest active/paused
2. Finalize session as `cancelled`
3. Keep worktree (user may want to review)

**Output:**
```
Session rs_abc123 cancelled
Sandbox kept at: .awos/worktrees/abc123
```

---

## Files to change

| File | Change |
|------|--------|
| `awos.py` | Add `worker` subcommand group with 5 sub-commands |

No kernel changes — pure UX wrapper.

---

## Implementation details

### Session ID resolution

```python
def resolve_session_id(session_id: Optional[str], status_filter: list[SessionStatus]) -> RuntimeSession:
    store = RuntimeSessionStore()
    if session_id:
        return store.load(session_id)
    
    # Find latest matching status
    sessions = [s for s in store.list_all() if s.status in status_filter]
    if not sessions:
        return None
    return max(sessions, key=lambda s: s.updated_at)
```

### Status formatting

```python
def format_status_line(session: RuntimeSession) -> str:
    prog = session.progress
    cost = session.budget.get("spent_usd", 0)
    goal_short = session.goal[:40] + "..." if len(session.goal) > 40 else session.goal
    return f"{session.session_id}  {session.status.value:8s}  {len(prog.completed_task_ids)}/{prog.total_tasks} tasks  ${cost:.2f}  \"{goal_short}\""
```

---

## CLI structure

```
awos worker
  ├── start "<goal>"           # new session
  ├── status                    # list all
  ├── resume [session_id]       # continue paused
  ├── diff [session_id]         # show sandbox changes
  └── cancel [session_id]       # stop work
```

---

## Testing

1. Unit test: session ID resolution logic
2. Manual smoke test:
   - `awos worker start "test goal"`
   - Ctrl+C during run
   - `awos worker status` shows paused
   - `awos worker resume` continues
   - `awos worker diff` shows changes
   - `awos worker cancel` stops it

---

## Success criteria

- [x] Research doc
- [ ] Spec doc (this file)
- [ ] Task file
- [ ] Implement `cmd_worker_*` in `awos.py`
- [ ] Manual smoke test (all 5 commands)
- [ ] Update `README.md` to show `awos worker` as primary interface
- [ ] Update `docs/product/stage1_subscription_worker_guideline.md` with new commands
