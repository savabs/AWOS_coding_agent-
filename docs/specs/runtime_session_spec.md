---
title: "Spec: RuntimeSession v1"
tags:
  - doc/spec
  - phase/stage-1
  - topic/runtime
  - layer/kernel
---

# Spec: RuntimeSession v1

> **Purpose:** Unified durable session state for multi-hour goals — pause, resume, fork, and checkpoint without losing plan, progress, or sandbox context.
> **Stage:** 1 exit criterion — multi-hour goal with pause/resume.
> **Philosophy:** [`VISION.md`](../../VISION.md) · **Plan:** [`docs/MASTER_PLAN.md`](../MASTER_PLAN.md)

---

## Goal

Ship a **single session record** (`.awos/sessions/rs_*.json`) that the orchestrator checkpoints during execution so an operator can pause mid-run, resume hours later, or fork a session — without re-planning from scratch.

**Exit condition:** `tests/test_runtime_session.py` passes; `awos sessions list` shows a paused session; `awos sessions resume <id>` skips completed tasks and continues the same goal; orchestrator writes a checkpoint after every task.

---

## Research Reference

| Source | Insight |
|--------|---------|
| `docs/specs/multi_session_agent_spec.md` | Per-goal task skip via `AgentStateManager` — **superseded** by session-ID model |
| `scaffold/agent/project_planner.py` | Goal DAG already persists at `.awos/goals/` — session stores **pointer**, not duplicate graph |
| `scaffold/agent/worktree.py` | Isolated execution exists but unwired — session stores sandbox fields for step 3 |
| `docs/memory/checkpoint_2026-06-16_master_plan.md` | P0 kernel runtime; worktree-first sandbox (no Docker) |
| `docs/research/bare_metal_compute_engine.md` | Future shared KV keyed by `session_id` — schema reserves `session_id` as stable ID |

No separate research doc required — prior art is in-repo.

---

## Problem with current state

Three overlapping persistence layers, none sufficient alone:

| Layer | Path | Gap |
|-------|------|-----|
| `AgentStateManager` | `.awos/state/<goal_hash>.json` | Goal-keyed only; no session ID; no pause; no sandbox; no budget snapshot |
| `ProjectPlanner` | `.awos/goals/<goal_id>.json` | DAG only; no run-time execution cursor |
| `ReasoningTraceStore` | `.awos/traces/orch_*.json` | Post-hoc traces; not resumable execution state |

`RuntimeSession` unifies the **execution cursor** (which tasks done, which wave, budget spent, sandbox path) while **referencing** existing goal-graph and trace stores by ID.

---

## Files Affected

**New files (implementation phases):**
- `scaffold/agent/runtime_session.py` — `RuntimeSession` dataclass + `RuntimeSessionStore`
- `scaffold/agent/virtual_execution_runtime.py` — worktree lifecycle (step 3 only)
- `tests/test_runtime_session.py` — unit tests for store + schema

**Modified files (later steps — not step 1):**
- `scaffold/agent/orchestrator.py` — checkpoint hooks, resume by `session_id`, cancel flag
- `awos.py` — `awos sessions` subcommand
- `scaffold/agent/gui_chat.py` — pass cancel token + session id (step 4)

**Files explicitly NOT touched in v1:**
- `VISION.md`, `docs/MASTER_PLAN.md` — link only
- `compute/` — Stage 2
- `kernel/` migration — deferred

---

## Session record schema (v1)

**Path:** `.awos/sessions/rs_<id>.json` where `<id>` is 12 hex chars (`uuid4().hex[:12]`).

**Atomic write:** write to `rs_<id>.json.tmp` then `os.replace()` (same pattern as `ProjectPlanner`).

```json
{
  "schema_version": 1,
  "session_id": "rs_a1b2c3d4e5f6",
  "goal": "add user authentication",
  "status": "paused",
  "created_at": "2026-06-17T10:00:00Z",
  "updated_at": "2026-06-17T10:45:00Z",
  "paused_at": "2026-06-17T10:45:00Z",
  "codebase_root": "/path/to/repo",
  "reasoning_session_id": "orch_37afb25b",
  "goal_graph_id": "g_abc123def456",
  "progress": {
    "completed_task_ids": [1, 2],
    "failed_task_ids": [],
    "current_task_id": 3,
    "total_tasks": 8,
    "decomposition_depth": 0
  },
  "budget": {
    "spent_usd": 0.042
  },
  "sandbox": {
    "enabled": false,
    "feature_id": null,
    "worktree_path": null,
    "branch": null
  },
  "parent_session_id": null,
  "cancel_requested": false
}
```

### Status enum

| Status | Meaning |
|--------|---------|
| `pending` | Created, orchestrator not started |
| `running` | Orchestrator active |
| `paused` | Checkpointed; safe to resume |
| `completed` | All tasks done, success |
| `failed` | Terminal failure |
| `cancelled` | User/GUI stop; partial progress kept |

**Transitions:** `pending → running → paused | completed | failed | cancelled`. Resume: `paused → running`. Fork creates new `pending` from parent snapshot.

### Field rules

- `goal_graph_id` — root id from `ProjectPlanner.load_or_create_goal()`; graph body stays in `.awos/goals/`.
- `reasoning_session_id` — links to `ReasoningTraceStore` / GUI run view (`orch_*`).
- `progress.tasks_snapshot` — **optional** in v1; if present, list of task dicts from last plan (enables replan-skip without re-calling planner on resume). Omit when empty; resume re-plans if missing.
- `cancel_requested` — set by GUI/CLI; orchestrator clears on start, checks between tasks.
- `sandbox` — populated in step 3 when `VirtualExecutionRuntime` is active.

---

## Relationship to `AgentStateManager`

**Decision: wrap, do not delete (v1).**

1. New runs create a `RuntimeSession` first; orchestrator still mirrors `completed_task_ids` / `failed_task_ids` into `AgentStateManager` for backward compat with `awos goals` and `--resume` by goal text.
2. `awos run --resume` (goal-based) resolves to the **latest** `paused` or `running` session for that goal hash, if any; else falls back to `AgentStateManager` only.
3. `awos sessions resume <session_id>` is the **preferred** resume path.
4. Deprecate `AgentStateManager` in a later cleanup task once `awos sessions` is proven — out of scope for v1.

---

## Checkpoint policy

| Event | Action |
|-------|--------|
| Session created | Write `pending` → `running` |
| After each task success/fail | Update `progress`, `budget.spent_usd`, `updated_at` |
| Cooperative cancel (GUI Stop, `cancel_requested`) | After current task: `status=cancelled`, checkpoint |
| SIGINT (Ctrl+C) | Same as pause: `status=paused` after current task |
| Normal completion | `status=completed` |
| Unrecoverable error | `status=failed` |
| Explicit pause API (future) | `status=paused`, set `paused_at` |

**Not in v1:** mid-task checkpoint (worker mid-edit). Boundary is **between tasks**.

---

## Implementation Steps

### Step 1: Spec + task file

What to do:
- This document + `tasks/active/runtime_session_task.md`

Verification: files exist; task checkboxes match steps below.

---

### Step 2: `runtime_session.py` + orchestrator checkpoints

What to do:
- Implement `RuntimeSession` dataclass, `RuntimeSessionStore` (CRUD, list, atomic save)
- Add `session_id: Optional[str] = None` to `execute_feature()`
- On run start: `store.create(goal, codebase_root)` or `store.load(session_id)`
- After each task in execution loop: `store.checkpoint(session, progress_delta)`
- On exit (success/fail/cancel): `store.finalize(session, status)`
- Register SIGINT handler that sets pause (only when `AWOS_RUNTIME_SESSION=1`)

Verification: `pytest tests/test_runtime_session.py -q` passes; manual run creates `.awos/sessions/rs_*.json`.

Edge cases:
- Corrupt JSON → log warning, start fresh session
- Resume session not `paused` → raise `SessionResumeError`
- Two resumes same session → second gets error (advisory lock via `status=running`)

---

### Step 3: `virtual_execution_runtime.py` + wire `WorktreeManager`

What to do:
- `VirtualExecutionRuntime` wraps `WorktreeManager` with session-scoped `feature_id`
- On session start (when `AWOS_USE_WORKTREE=true`): create worktree, store paths in `sandbox`
- On resume: reattach to existing worktree if path exists
- On complete/cancel: optional merge (`AWOS_WORKTREE_AUTO_MERGE=true`) or leave branch for review
- Pass `codebase_root=worktree_path` to worker for isolated edits

Verification: with worktree enabled, session JSON has `sandbox.worktree_path`; edits land in worktree not main.

Edge cases:
- Not a git repo → `sandbox.enabled=false`, run in-place (current behavior)
- Stale worktree path → recreate worktree, log warning

---

### Step 4: `awos sessions` CLI + orchestrator cancel hook

What to do:
- CLI subcommands: `list`, `show <id>`, `resume <id>`, `fork <id>`
- `list`: id, status, goal (truncated), updated_at, progress fraction
- `resume`: calls `execute_feature(session_id=...)`
- `fork`: copy progress snapshot to new `rs_*` with `parent_session_id`
- Orchestrator: `threading.Event` `_cancel` checked between tasks; GUI sets `cancel_requested` on session file + event
- `awos run --session <id>` alias for resume

Verification: Stop in GUI mid-run → orchestrator exits within one task boundary; `awos sessions list` shows `cancelled` or `paused`.

---

## Interface Contract

```python
# scaffold/agent/runtime_session.py

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class SessionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class SessionProgress:
    completed_task_ids: list[Any] = field(default_factory=list)
    failed_task_ids: list[Any] = field(default_factory=list)
    current_task_id: Optional[Any] = None
    total_tasks: int = 0
    decomposition_depth: int = 0


@dataclass
class SessionSandbox:
    enabled: bool = False
    feature_id: Optional[str] = None
    worktree_path: Optional[str] = None
    branch: Optional[str] = None


@dataclass
class RuntimeSession:
    schema_version: int = 1
    session_id: str = ""
    goal: str = ""
    status: SessionStatus = SessionStatus.PENDING
    created_at: str = ""
    updated_at: str = ""
    paused_at: Optional[str] = None
    codebase_root: str = "."
    reasoning_session_id: Optional[str] = None
    goal_graph_id: Optional[str] = None
    progress: SessionProgress = field(default_factory=SessionProgress)
    budget: dict[str, float] = field(default_factory=lambda: {"spent_usd": 0.0})
    sandbox: SessionSandbox = field(default_factory=SessionSandbox)
    parent_session_id: Optional[str] = None
    cancel_requested: bool = False

    def to_dict(self) -> dict[str, Any]: ...
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeSession: ...


class SessionResumeError(Exception):
    """Raised when resume preconditions are not met."""


class RuntimeSessionStore:
    """CRUD for .awos/sessions/rs_*.json"""

    SESSIONS_DIR = Path(".awos/sessions")

    def __init__(self, sessions_dir: Optional[str] = None) -> None: ...

    def create(self, goal: str, codebase_root: str = ".") -> RuntimeSession:
        """New rs_* record in pending state."""

    def load(self, session_id: str) -> RuntimeSession:
        """Load by id; raises FileNotFoundError if missing."""

    def save(self, session: RuntimeSession) -> None:
        """Atomic write via .tmp + os.replace."""

    def list_sessions(
        self,
        status: Optional[SessionStatus] = None,
        goal: Optional[str] = None,
    ) -> list[RuntimeSession]:
        """Newest first."""

    def find_latest_for_goal(self, goal: str) -> Optional[RuntimeSession]:
        """Latest paused or running session for goal text (normalized hash)."""

    def request_cancel(self, session_id: str) -> None:
        """Set cancel_requested=true; orchestrator polls between tasks."""

    def fork(self, session_id: str, new_goal: Optional[str] = None) -> RuntimeSession:
        """Copy progress; new session_id; parent_session_id set."""

    def checkpoint(
        self,
        session: RuntimeSession,
        *,
        completed_task_id: Any = None,
        failed_task_id: Any = None,
        current_task_id: Any = None,
        spent_usd: Optional[float] = None,
    ) -> RuntimeSession:
        """Update progress + budget; save."""

    def finalize(self, session: RuntimeSession, status: SessionStatus) -> RuntimeSession:
        """Terminal state; clear cancel_requested."""
```

Input constraints:
- `session_id` must match `rs_[0-9a-f]{12}`
- `goal` non-empty on create

Output guarantees:
- `save()` is crash-safe (atomic replace)
- `list_sessions()` skips corrupt files with warning log
- `fork()` never mutates parent session

---

## Orchestrator integration (step 2 detail)

```python
# execute_feature() signature addition
def execute_feature(
    self,
    goal: str,
    codebase_root: str = ".",
    ...
    resume: bool = False,
    session_id: Optional[str] = None,
    cancel_event: Optional[threading.Event] = None,
) -> dict:
```

**Start path:**
```
if session_id:
    rs = store.load(session_id); assert rs.status == PAUSED
elif resume:
    rs = store.find_latest_for_goal(goal) or create_new
else:
    rs = store.create(goal, codebase_root)
rs.status = RUNNING; store.save(rs)
self._runtime_session = rs
self._cancel = cancel_event or threading.Event()
```

**Task loop (after each task):**
```
if self._cancel.is_set() or rs.cancel_requested:
    store.finalize(rs, CANCELLED); return partial_result
store.checkpoint(rs, completed_task_id=..., spent_usd=ledger.total)
```

**Resume skip (replaces/enhances current AgentStateManager block):**
```
if rs.progress.completed_task_ids:
    tasks_to_run = [t for t in tasks if t["task_id"] not in rs.progress.completed_task_ids]
```

---

## Edge Cases

| Scenario | Expected behavior |
|----------|-------------------|
| Corrupt session JSON | Skip in list; load raises clear error |
| Resume `completed` session | `SessionResumeError` with message |
| Resume while another process holds `running` | Second resume fails (status check) |
| Goal text changed, same session_id | Not allowed — fork instead |
| No git repo + worktree | `sandbox.enabled=false`; session still works |
| `--resume` goal with no session file | Fall back to `AgentStateManager` task skip |
| Budget exhausted mid-session | Existing budget hard-stop; session → `paused` with reason in log |
| `AWOS_RUNTIME_SESSION` unset | Feature off; legacy behavior only (step 2 gates on env var initially) |

**Feature flag (step 2):** `AWOS_RUNTIME_SESSION=true` enables checkpoint writes. Default `false` until step 4 proven, then flip default.

---

## Testing Plan

**Unit tests** (`tests/test_runtime_session.py`):
- `test_create_session` — file exists, schema_version=1, status=pending
- `test_save_atomic` — corrupt partial .tmp does not destroy prior file
- `test_checkpoint_updates_progress` — completed_task_ids grows
- `test_list_sessions_filter_status` — paused only
- `test_find_latest_for_goal` — normalized goal matching
- `test_fork_copies_progress` — new id, parent link, independent files
- `test_resume_error_wrong_status` — completed → SessionResumeError
- `test_from_dict_missing_fields` — defaults applied
- `test_request_cancel_flag` — cancel_requested persisted

**Integration tests** (`tests/test_runtime_session_integration.py` — step 2):
- Mock orchestrator runs 2 tasks, kill mid-loop → session paused with 1 completed
- Resume skips task 1

**Manual verification:**
```bash
export AWOS_RUNTIME_SESSION=true
python3 awos.py run --goal "smoke test session"
# Ctrl+C after task 1
python3 awos.py sessions list
python3 awos.py sessions resume rs_<id>
```

---

## Rollback Plan

- Set `AWOS_RUNTIME_SESSION=false` (or unset) — orchestrator ignores session store
- Delete `.awos/sessions/` — no impact on goals/traces/state
- Revert `orchestrator.py` checkpoint hooks — `AgentStateManager` still works
- No DB migrations

---

## Deferred (explicitly out of v1)

- HTTP API for sessions (Stage 7)
- Mid-task worker checkpoint (file edit in flight)
- Docker/microVM sandbox
- `kernel/` package move
- Auto-cleanup of old sessions (manual `rm` for now)
- Verify-fail → minimal graph mutation (Stage 1 criterion #4 — separate spec)

---

## Related

- [`docs/MASTER_PLAN.md`](../MASTER_PLAN.md) — P0 priorities
- [`docs/specs/multi_session_agent_spec.md`](multi_session_agent_spec.md) — superseded cursor model
- [`docs/specs/project_planner_spec.md`](project_planner_spec.md) — goal DAG
- [`tasks/active/runtime_session_task.md`](../../tasks/active/runtime_session_task.md) — implementation tracker
- [`scaffold/agent/worktree.py`](../../scaffold/agent/worktree.py) — sandbox backend (step 3)
