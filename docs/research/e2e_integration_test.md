# End-to-End Integration Test — Research

## Problem
The current tests mock individual components in isolation.
No test exercises the full pipeline: Orchestrator → Planner → Worker → Verifier → GitManager.
If something breaks at the boundary between components, tests pass but the agent is broken.

## What to test
Full `Orchestrator.execute_feature()` with:
- **Real file I/O** (temp directory)
- **Mocked LLM calls** (Planner, Worker, Verifier all return fixed responses, no API key needed)
- **Real GitManager** (in-memory backups, no actual git)
- **Real Verifier** (syntax checking on actual Python files)
- **Real DAGExecutor** (use pre_planned_tasks to bypass Planner)

## How to mock
Python `unittest.mock.patch`:

```python
from unittest.mock import patch, MagicMock

# Mock Planner.plan() to return fixed tasks
with patch("scaffold.agent.orchestrator.Planner") as MockPlanner:
    MockPlanner.return_value.plan.return_value = {
        "plan": [{"task_id": 1, "file": "hello.py", "action": "...", "complexity": "low"}],
        "reasoning": "test",
        "total_tasks": 1,
    }
```

Or simpler: use `pre_planned_tasks` kwarg to bypass Planner entirely (already supported).

## Mock Worker response
Worker must return a valid SEARCH/REPLACE dict:
```python
{
    "success": True,
    "search": "# placeholder",
    "replace": "def hello():\n    return 'world'",
    "explanation": "Added hello function",
}
```

## Test scenario
1. Create temp dir with a Python file containing `# placeholder`
2. Run `orchestrator.execute_feature(goal=..., pre_planned_tasks=[...], codebase_root=tmp)`
3. Mock `Worker.execute_task()` to return the SEARCH/REPLACE above
4. Assert: file was modified, execution_log has status=completed, result["success"] is True

## No API key needed
Mocking Planner (or using pre_planned_tasks) + mocking Worker + real Verifier = zero API calls.
This test can run in CI without any secrets.
