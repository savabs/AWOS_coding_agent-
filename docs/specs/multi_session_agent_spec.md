# Multi-Session Agent — Spec

## New file: `scaffold/agent/agent_state_manager.py`

```python
"""
AgentStateManager — persist per-goal execution state across sessions.
State file: .awos/state/<goal_hash>.json
"""
import hashlib
import json
import time
from pathlib import Path
from typing import Optional


class AgentStateManager:
    """Load and save per-goal agent state between runs."""

    STATE_DIR = Path(".awos/state")

    def __init__(self, state_dir: Optional[str] = None) -> None:
        self.state_dir = Path(state_dir) if state_dir else self.STATE_DIR
        self.state_dir.mkdir(parents=True, exist_ok=True)

    # ── Public API ─────────────────────────────────────────────────────

    def load(self, goal: str) -> dict:
        """
        Load existing state for a goal, or return empty state dict.
        Returns: {goal, goal_hash, completed_task_ids, failed_task_ids, session_ids, status}
        """
        path = self._path(goal)
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                pass
        return self._empty(goal)

    def save(self, state: dict) -> None:
        """Persist state to disk."""
        state["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        path = self._path(state["goal"])
        path.write_text(json.dumps(state, indent=2))

    def mark_complete(self, state: dict, task_id) -> dict:
        """Mark a task as completed and save."""
        if task_id not in state["completed_task_ids"]:
            state["completed_task_ids"].append(task_id)
        state["failed_task_ids"] = [t for t in state["failed_task_ids"] if t != task_id]
        self.save(state)
        return state

    def mark_failed(self, state: dict, task_id) -> dict:
        """Mark a task as failed and save."""
        if task_id not in state["failed_task_ids"]:
            state["failed_task_ids"].append(task_id)
        self.save(state)
        return state

    def add_session(self, state: dict, session_id: str) -> dict:
        """Record a reasoning session ID against this goal."""
        if session_id not in state["session_ids"]:
            state["session_ids"].append(session_id)
        self.save(state)
        return state

    def finalize(self, state: dict, success: bool) -> dict:
        """Mark goal as complete or failed."""
        state["status"] = "complete" if success else "failed"
        self.save(state)
        return state

    def list_goals(self) -> list[dict]:
        """List all tracked goals with their status."""
        goals = []
        for f in self.state_dir.glob("*.json"):
            try:
                goals.append(json.loads(f.read_text()))
            except Exception:
                pass
        return sorted(goals, key=lambda x: x.get("last_updated", ""), reverse=True)

    # ── Private ───────────────────────────────────────────────────────

    def _path(self, goal: str) -> Path:
        h = hashlib.sha256(goal.strip().lower().encode()).hexdigest()[:10]
        return self.state_dir / f"{h}.json"

    @staticmethod
    def _empty(goal: str) -> dict:
        return {
            "goal": goal,
            "goal_hash": hashlib.sha256(goal.strip().lower().encode()).hexdigest()[:10],
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "last_updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "completed_task_ids": [],
            "failed_task_ids": [],
            "session_ids": [],
            "status": "in_progress",
        }
```

---

## Change: `scaffold/agent/orchestrator.py`

### Import at top
```python
try:
    from .agent_state_manager import AgentStateManager
except ImportError:
    from agent_state_manager import AgentStateManager
```

### In `__init__`
```python
self.state_manager = AgentStateManager()
```

### In `execute_feature()` — add `resume: bool = False` parameter

After planning, before the while loop:
```python
# Multi-session resume: skip already-completed tasks
agent_state = self.state_manager.load(goal)
self.state_manager.add_session(agent_state, session.session_id)

if resume and agent_state["completed_task_ids"]:
    already_done = set(agent_state["completed_task_ids"])
    skipped = [t for t in tasks if t["task_id"] in already_done]
    tasks_to_run = [t for t in tasks if t["task_id"] not in already_done]
    print(f"[RESUME] Skipping {len(skipped)} already-completed task(s): {list(already_done)}")
else:
    tasks_to_run = list(tasks)
```

At the end of `_execute_single_task()` — after `return` line, update state:

Actually: in `_run_task_batch`, after collecting results:
```python
for r in results:
    if r["success"]:
        self.state_manager.mark_complete(agent_state, r["task_id"])
    else:
        self.state_manager.mark_failed(agent_state, r["task_id"])
```

Then at the end of `execute_feature()`, before returning:
```python
self.state_manager.finalize(agent_state, overall_success)
```

**Problem:** `agent_state` needs to be accessible in `_run_task_batch` and at the end of `execute_feature`.
**Solution:** Store it on `self` as `self._current_state = agent_state` at the start of `execute_feature`.

---

## Change: `awos.py` — `cmd_run()`

Add `--resume` flag:
```python
parser_run.add_argument("--resume", action="store_true", help="Resume from last run for this goal")
```

Pass to orchestrator:
```python
result = orch.execute_feature(goal=args.goal, codebase_root=args.root, resume=args.resume)
```

Also add `goals` subcommand:
```python
# awos goals — list tracked goals
def cmd_goals(args):
    from scaffold.agent.agent_state_manager import AgentStateManager
    mgr = AgentStateManager()
    for g in mgr.list_goals():
        print(f"[{g['status'].upper():10}] {g['goal'][:60]}  ({g['last_updated']})")
```

---

## Test: `test_multi_session_agent.py`

```python
import tempfile, os
from scaffold.agent.agent_state_manager import AgentStateManager

with tempfile.TemporaryDirectory() as tmp:
    mgr = AgentStateManager(state_dir=tmp)

    # Test 1: load returns empty state for new goal
    state = mgr.load("build login system")
    assert state["status"] == "in_progress"
    assert state["completed_task_ids"] == []

    # Test 2: mark_complete persists
    mgr.mark_complete(state, task_id=1)
    state2 = mgr.load("build login system")
    assert 1 in state2["completed_task_ids"]

    # Test 3: mark_failed persists
    mgr.mark_failed(state2, task_id=2)
    state3 = mgr.load("build login system")
    assert 2 in state3["failed_task_ids"]

    # Test 4: finalize sets status
    mgr.finalize(state3, success=True)
    state4 = mgr.load("build login system")
    assert state4["status"] == "complete"

    # Test 5: different goals = different files
    state_b = mgr.load("add search feature")
    assert state_b["completed_task_ids"] == []

    # Test 6: list_goals returns both goals
    goals = mgr.list_goals()
    assert len(goals) == 2
```
