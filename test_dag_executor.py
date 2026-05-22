#!/usr/bin/env python3
"""
Smoke test for DAGExecutor — wave building + parallel execution logic.
No API keys required.
"""
import sys
import os
import time
import threading

sys.path.insert(0, "scaffold")
sys.path.insert(0, "scaffold/agent")
os.environ.setdefault("AWOS_MONTHLY_BUDGET", "20.0")

from agent.dag_executor import DAGExecutor

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results = []


def assert_test(name, condition, detail=""):
    if condition:
        results.append((name, True, detail))
        print(f"  {PASS} {name}")
    else:
        results.append((name, False, detail))
        print(f"  {FAIL} {name} — {detail}")


print("=" * 60)
print("DAGExecutor Smoke Test")
print("=" * 60)

# ── 1. All different files → single wave ──────────────────────────────
print("\n1. All different files → single wave")
tasks_all_diff = [
    {"task_id": 1, "file": "a.py", "action": "add x"},
    {"task_id": 2, "file": "b.py", "action": "add y"},
    {"task_id": 3, "file": "c.py", "action": "add z"},
]
dag = DAGExecutor(tasks_all_diff)
assert_test("single_wave", dag.wave_count == 1, f"got {dag.wave_count}")
assert_test("all_in_wave_0", len(dag.waves[0]) == 3, f"got {len(dag.waves[0])}")
assert_test("parallelism_1_0", dag.parallelism_score == 1.0, f"got {dag.parallelism_score}")

# ── 2. Same file tasks → sequential waves ─────────────────────────────
print("\n2. Same file tasks → sequential waves")
tasks_same_file = [
    {"task_id": 1, "file": "auth.py", "action": "step 1"},
    {"task_id": 2, "file": "auth.py", "action": "step 2"},
    {"task_id": 3, "file": "auth.py", "action": "step 3"},
]
dag2 = DAGExecutor(tasks_same_file)
assert_test("three_waves", dag2.wave_count == 3, f"got {dag2.wave_count}")
assert_test("wave0_has_t1", dag2.waves[0][0]["task_id"] == 1)
assert_test("wave1_has_t2", dag2.waves[1][0]["task_id"] == 2)
assert_test("wave2_has_t3", dag2.waves[2][0]["task_id"] == 3)
assert_test("low_parallelism", dag2.parallelism_score < 0.5, f"got {dag2.parallelism_score}")

# ── 3. Mixed: some shared, some independent ───────────────────────────
print("\n3. Mixed files")
tasks_mixed = [
    {"task_id": 1, "file": "auth.py", "action": "create"},
    {"task_id": 2, "file": "models.py", "action": "create"},  # independent
    {"task_id": 3, "file": "auth.py", "action": "extend"},   # depends on 1
    {"task_id": 4, "file": "app.py", "action": "wire"},       # independent
]
dag3 = DAGExecutor(tasks_mixed)
assert_test("two_waves", dag3.wave_count == 2, f"got {dag3.wave_count}")
wave0_ids = {t["task_id"] for t in dag3.waves[0]}
wave1_ids = {t["task_id"] for t in dag3.waves[1]}
assert_test("wave0_has_1_2_4", wave0_ids == {1, 2, 4}, f"got {wave0_ids}")
assert_test("wave1_has_3", wave1_ids == {3}, f"got {wave1_ids}")

# ── 4. Explicit depends_on field ──────────────────────────────────────
print("\n4. Explicit depends_on")
tasks_explicit = [
    {"task_id": 1, "file": "a.py", "action": "first"},
    {"task_id": 2, "file": "b.py", "action": "second", "depends_on": [1]},
    {"task_id": 3, "file": "c.py", "action": "third",  "depends_on": [1]},
    {"task_id": 4, "file": "d.py", "action": "fourth", "depends_on": [2, 3]},
]
dag4 = DAGExecutor(tasks_explicit)
assert_test("explicit_wave0", {t["task_id"] for t in dag4.waves[0]} == {1})
assert_test("explicit_wave1", {t["task_id"] for t in dag4.waves[1]} == {2, 3})
assert_test("explicit_wave2", {t["task_id"] for t in dag4.waves[2]} == {4})
assert_test("explicit_3_waves", dag4.wave_count == 3, f"got {dag4.wave_count}")

# ── 5. Empty task list ────────────────────────────────────────────────
print("\n5. Empty task list")
dag5 = DAGExecutor([])
assert_test("empty_waves", dag5.waves == [])
assert_test("empty_score", dag5.parallelism_score == 0.0)

# ── 6. Single task ────────────────────────────────────────────────────
print("\n6. Single task")
dag6 = DAGExecutor([{"task_id": 1, "file": "only.py", "action": "do it"}])
assert_test("single_one_wave", dag6.wave_count == 1)
assert_test("single_score", dag6.parallelism_score == 1.0)

# ── 7. execute() with mock task_fn ───────────────────────────────────
print("\n7. execute() — concurrent execution order")
call_log = []
lock = threading.Lock()

def mock_fn(task):
    time.sleep(0.02)  # simulate I/O wait
    with lock:
        call_log.append(task["task_id"])
    return {"task_id": task["task_id"], "success": True, "task": task}

dag7 = DAGExecutor(tasks_mixed)  # waves: [1,2,4] then [3]
res = dag7.execute(mock_fn)
assert_test("execute_returns_all", len(res) == 4, f"got {len(res)}")
assert_test("all_success", all(r["success"] for r in res))
# task 3 must appear AFTER tasks 1, 2, 4 in call_log
idx_1 = call_log.index(1)
idx_3 = call_log.index(3)
assert_test("task3_after_task1", idx_3 > idx_1, f"call_log={call_log}")

# ── 8. execute() — exception handling ────────────────────────────────
print("\n8. execute() — exception in task_fn is caught")

def failing_fn(task):
    if task["task_id"] == 2:
        raise RuntimeError("simulated API failure")
    return {"task_id": task["task_id"], "success": True, "task": task}

dag8 = DAGExecutor(tasks_all_diff)
res8 = dag8.execute(failing_fn)
assert_test("exception_caught", len(res8) == 3, f"got {len(res8)}")
failed = [r for r in res8 if not r["success"]]
assert_test("failed_is_t2", failed[0]["task_id"] == 2)
assert_test("error_field_set", "error" in failed[0])

# ── 9. Cycle guard ────────────────────────────────────────────────────
print("\n9. Cycle guard (circular depends_on)")
tasks_cycle = [
    {"task_id": 1, "file": "a.py", "action": "x", "depends_on": [2]},
    {"task_id": 2, "file": "b.py", "action": "y", "depends_on": [1]},
]
dag9 = DAGExecutor(tasks_cycle)
assert_test("cycle_resolved", dag9.wave_count >= 1)
all_ids = {t["task_id"] for w in dag9.waves for t in w}
assert_test("cycle_all_tasks", all_ids == {1, 2}, f"got {all_ids}")

# ── 10. Orchestrator: _build_waves accessible via static ──────────────
print("\n10. _build_waves static method")
waves = DAGExecutor._build_waves(tasks_mixed)
assert_test("static_call_works", len(waves) == 2, f"got {len(waves)}")

# ═════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)

passed = sum(1 for _, ok, _ in results if ok)
failed_count = sum(1 for _, ok, _ in results if not ok)

for name, ok, detail in results:
    mark = PASS if ok else FAIL
    print(f"  {mark} {name}")
    if not ok and detail:
        print(f"      → {detail}")

print()
print(f"Total: {passed}/{len(results)} passed  |  {failed_count} failed")

if failed_count:
    print("\nSome tests FAILED.")
    sys.exit(1)
else:
    print("\nAll DAGExecutor tests PASSED.")
    sys.exit(0)
