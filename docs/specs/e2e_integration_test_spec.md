# E2E Integration Test — Spec

## Output: `test_e2e_pipeline.py` (new file at project root)

Uses `unittest.mock.patch` + `tempfile` — zero API calls required.

---

## Full file content

```python
#!/usr/bin/env python3
"""
End-to-end pipeline test for Orchestrator.
Mocks LLM calls (Planner, Worker). Uses real Verifier, GitManager, DAGExecutor.
No API keys required.
"""
import sys, os, tempfile, shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, "scaffold")

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results = []

def check(name, cond, detail=""):
    results.append((name, cond, detail))
    print(f"  {PASS if cond else FAIL} {name}" + (f" — {detail}" if not cond and detail else ""))

# ── Setup: temp codebase dir ──────────────────────────────────────────
tmp_dir = tempfile.mkdtemp(prefix="awos_e2e_")
test_file = Path(tmp_dir) / "hello.py"
test_file.write_text("# placeholder\n")

print("=" * 60)
print("E2E Pipeline Test")
print("=" * 60)

# ── Pre-planned tasks (bypasses Planner API call) ─────────────────────
pre_planned = [
    {
        "task_id": 1,
        "file": "hello.py",
        "action": "Add hello function",
        "complexity": "low",
        "constraints": ["function must be named hello"],
        "must_not": ["use global state"],
    }
]

# Mock Worker response — valid SEARCH/REPLACE for the Verifier
MOCK_WORKER_RESULT = {
    "success": True,
    "search": "# placeholder",
    "replace": "def hello():\n    return 'world'\n",
    "explanation": "Added hello function",
}

# ── Test 1: Basic happy path ──────────────────────────────────────────
print("\n1. Happy path — task planned, worker succeeds, verifier applies")

with patch("agent.worker.Worker.execute_task", return_value=MOCK_WORKER_RESULT):
    try:
        from agent.orchestrator import Orchestrator
        orch = Orchestrator()
        result = orch.execute_feature(
            goal="add hello function",
            codebase_root=tmp_dir,
            pre_planned_tasks=pre_planned,
        )
        check("result_is_dict", isinstance(result, dict))
        check("success_true", result.get("success") is True, str(result.get("errors")))
        check("tasks_completed_1", result.get("tasks_completed") == 1, str(result))
        check("tasks_failed_0", result.get("tasks_failed") == 0)
        check("execution_log_populated", len(result.get("execution_log", [])) == 1)
        log_entry = result["execution_log"][0]
        check("log_status_completed", log_entry.get("status") == "completed", str(log_entry))

        # File should now contain the new function
        content = test_file.read_text()
        check("file_modified", "def hello():" in content, f"content={content[:80]!r}")
        check("placeholder_replaced", "# placeholder" not in content)

    except Exception as exc:
        check("no_exception", False, str(exc))

# ── Test 2: Worker fails → task marked failed, file rolled back ───────
print("\n2. Worker failure — file rolled back, task marked failed")

# Reset file
test_file.write_text("# placeholder\n")

MOCK_WORKER_FAIL = {"success": False, "error": "model returned garbage", "search": "", "replace": ""}

with patch("agent.worker.Worker.execute_task", return_value=MOCK_WORKER_FAIL):
    try:
        orch2 = Orchestrator()
        result2 = orch2.execute_feature(
            goal="add hello function",
            codebase_root=tmp_dir,
            pre_planned_tasks=pre_planned,
        )
        check("fail_success_false", result2.get("success") is False)
        check("fail_tasks_failed_1", result2.get("tasks_failed", 0) >= 1)
        check("file_restored", test_file.read_text() == "# placeholder\n",
              repr(test_file.read_text()))
    except Exception as exc:
        check("no_exception_on_fail", False, str(exc))

# ── Test 3: Parallel execution (use_parallel=True) ────────────────────
print("\n3. Parallel execution — two independent tasks, different files")

file_a = Path(tmp_dir) / "module_a.py"
file_b = Path(tmp_dir) / "module_b.py"
file_a.write_text("# a\n")
file_b.write_text("# b\n")

parallel_tasks = [
    {"task_id": 1, "file": "module_a.py", "action": "add fn_a", "complexity": "low"},
    {"task_id": 2, "file": "module_b.py", "action": "add fn_b", "complexity": "low"},
]

def mock_worker_multi(task, **kwargs):
    fname = task["file"].replace(".py", "")
    return {
        "success": True,
        "search": f"# {fname[-1]}",
        "replace": f"def fn_{fname[-1]}(): pass\n",
        "explanation": "done",
    }

with patch("agent.worker.Worker.execute_task", side_effect=mock_worker_multi):
    try:
        orch3 = Orchestrator()
        result3 = orch3.execute_feature(
            goal="add module functions",
            codebase_root=tmp_dir,
            pre_planned_tasks=parallel_tasks,
            use_parallel=True,
        )
        check("parallel_success", result3.get("success") is True, str(result3.get("errors")))
        check("parallel_completed_2", result3.get("tasks_completed") == 2)
        check("file_a_modified", "def fn_a" in file_a.read_text(), file_a.read_text())
        check("file_b_modified", "def fn_b" in file_b.read_text(), file_b.read_text())
    except Exception as exc:
        check("no_parallel_exception", False, str(exc))

# ── Test 4: execution_log structure ───────────────────────────────────
print("\n4. execution_log entries have required fields")

with patch("agent.worker.Worker.execute_task", return_value=MOCK_WORKER_RESULT):
    test_file.write_text("# placeholder\n")
    orch4 = Orchestrator()
    result4 = orch4.execute_feature(
        goal="add hello function again",
        codebase_root=tmp_dir,
        pre_planned_tasks=pre_planned,
    )
    for entry in result4.get("execution_log", []):
        check("log_has_task_id", "task_id" in entry, str(entry))
        check("log_has_status", "status" in entry, str(entry))
        check("log_has_reason", "reason" in entry, str(entry))

# ── Test 5: result has all required keys ─────────────────────────────
print("\n5. Result dict has all required keys")
required_keys = ["success", "goal", "tasks_completed", "tasks_failed",
                  "total_tasks", "execution_log", "errors", "time_elapsed"]
for k in required_keys:
    check(f"result_has_{k}", k in result4, f"keys={list(result4.keys())}")

# ── Cleanup ───────────────────────────────────────────────────────────
shutil.rmtree(tmp_dir, ignore_errors=True)

# ── Summary ───────────────────────────────────────────────────────────
print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
print(f"Total: {passed}/{len(results)} passed  |  {failed} failed")
if failed:
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL {name}: {detail}")
    sys.exit(1)
else:
    print("All E2E pipeline tests PASSED.")
    sys.exit(0)
```

---

## Notes for implementer
- `pre_planned_tasks` kwarg bypasses Planner — no ANTHROPIC_API_KEY needed
- `patch("agent.worker.Worker.execute_task", ...)` mocks at the module level
- The Verifier runs for real — if `MOCK_WORKER_RESULT.search` is not in the file, Verifier will fail
- Make sure each test resets the file content before running (`test_file.write_text("# placeholder\n")`)
- `side_effect` on a mock accepts a callable that receives the same args as the real function
