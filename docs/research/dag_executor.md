# DAG-Based Parallel Executor — Research

## Problem
The Orchestrator runs tasks sequentially even when they are independent.
For a 4-task plan where tasks touch 4 different files, execution is 4× slower than necessary.
Measured gap: sequential execution could be 3–5× faster with parallelism (per effectiveness analysis).

## Solution Design

### Dependency Inference
Tasks carry a `file` field. Two tasks on **different files** are independent.
Tasks on the **same file** must run in order (task_id ascending) to avoid conflicting writes.
The Planner may optionally emit a `depends_on: [task_id, ...]` field for explicit ordering.

### Wave-Based Topological Sort
1. Build a dependency map:
   - Implicit: same-file tasks sorted by task_id → each depends on the previous
   - Explicit: `depends_on` field if present
2. Assign tasks to "waves" — a wave is the maximal set of tasks whose deps are all complete
3. Execute each wave concurrently with `ThreadPoolExecutor`

### Example
```
tasks = [
  {id:1, file:"auth.py"},   → wave 0
  {id:2, file:"models.py"}, → wave 0  (different file from id:1)
  {id:3, file:"auth.py"},   → wave 1  (same file as id:1, depends on id:1)
  {id:4, file:"app.py"},    → wave 0  (different file from all above)
]
waves = [[1, 2, 4], [3]]
Speed-up: 2 waves instead of 4 serial steps = up to 3× faster
```

## Thread Safety
- Each task touches exactly one file (Planner constraint: "each task must change ONLY ONE file")
- Python's GIL ensures dict/list operations (backup_file, record_modified, etc.) are atomic
- `concurrent.futures.ThreadPoolExecutor` with CPython = safe for I/O-bound work
- Max workers: 4 (caps concurrent API calls to stay within rate limits)

## Tech Choice
- `concurrent.futures.ThreadPoolExecutor` — stdlib, no new deps
- Tasks are I/O-bound (LLM API calls) → threading > multiprocessing
- Opt-in via `use_parallel=True` on `execute_feature()` — sequential default unchanged
