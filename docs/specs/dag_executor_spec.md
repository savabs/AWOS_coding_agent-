# DAGExecutor Spec

## Interface

### `DAGExecutor(tasks: list[dict])`
Constructor. Builds waves immediately on init.

### `.execute(task_fn: Callable[[dict], dict]) -> list[dict]`
Runs all tasks respecting wave order. Returns list of result dicts.
- Each wave runs concurrently with `ThreadPoolExecutor(max_workers=4)`
- Single-task waves skip the thread pool (no overhead)
- If a task raises, it is caught and returned as `{success: False, error: str}`

### `.waves: list[list[dict]]`
Read-only. The computed wave groupings.

### `.wave_count: int`
Number of waves.

### `.parallelism_score: float`
`max(wave_size) / total_tasks`. 1.0 = all tasks run in one wave (fully parallel).

## `DAGExecutor._build_waves(tasks)` (static)
1. Collect all task IDs
2. Infer same-file dependencies: within tasks sharing `task["file"]`, order by task_id
3. Merge with explicit `depends_on` field if present
4. Topological sort → assign wave levels
5. Cycle guard: if no ready tasks found, force lowest-id task to break cycle

## Orchestrator Changes

### `execute_feature(... use_parallel: bool = False)`
New `use_parallel` kwarg. Default `False` preserves existing behaviour.

### `_execute_single_task(task, codebase_root, codebase_context, sym_index, git, session) -> dict`
Extracted from the inner for-loop. Returns `{task_id, success, task}`.
Side effects (execution_log, git, example_store, performance, traces) remain inline.

### `_run_task_batch(tasks, ..., use_parallel) -> (completed, failed, failed_list)`
Wraps `_execute_single_task`:
- Sequential: `[_execute_single_task(t) for t in tasks]`
- Parallel: `DAGExecutor(tasks).execute(_execute_single_task)`

## Constraints
- No new pip dependencies (stdlib only)
- `use_parallel=False` is the default — zero behaviour change for existing callers
- Max 4 concurrent workers to avoid API rate limits
- Works with `TaskDecomposer` retry loop (decomposed tasks also get parallelised)
