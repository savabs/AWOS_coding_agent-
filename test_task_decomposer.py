#!/usr/bin/env python3
"""
Smoke test for TaskDecomposer — Retry with Simplification.

Tests heuristic decomposition, LLM decomposition, and sub-task properties.
No API keys required.
"""
import sys
import os

sys.path.insert(0, "scaffold")
sys.path.insert(0, "scaffold/agent")
os.environ.setdefault("AWOS_MONTHLY_BUDGET", "20.0")

from agent.task_decomposer import TaskDecomposer

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
print("TaskDecomposer Smoke Test")
print("=" * 60)

# ── 1. Heuristic: split by conjunctions ────────────────────────────────
print("\n1. Heuristic — Split by conjunctions")
td = TaskDecomposer()

task = {
    "task_id": "t1",
    "action": "Create auth module and then add login function and wire into app",
    "file": "auth.py",
    "complexity": "high",
}
subs = td.decompose(task)
assert_test("heuristic_count", len(subs) >= 2, f"got {len(subs)}")
assert_test("heuristic_max_cap", len(subs) <= 4, f"got {len(subs)}")
assert_test(
    "heuristic_parent_tracking",
    all(s.get("parent_task") == "t1" for s in subs),
)
assert_test(
    "heuristic_depth_incremented",
    all(s.get("decomposition_depth") == 1 for s in subs),
)
assert_test(
    "heuristic_complexity_lowered",
    all(s.get("complexity") in ("low", "medium") for s in subs),
)

# ── 2. Heuristic: split by action verbs ───────────────────────────────
print("\n2. Heuristic — Split by action verbs")
task2 = {
    "task_id": "t2",
    "action": "Implement user registration and create database schema",
    "file": "models.py",
    "complexity": "medium",
}
subs2 = td.decompose(task2)
assert_test("verb_split_count", len(subs2) >= 2, f"got {len(subs2)}")

# ── 3. Heuristic: no clear split → minimum 2 ───────────────────────────
print("\n3. Heuristic — Fallback when no clear split")
task3 = {
    "task_id": "t3",
    "action": "Fix typo in README",
    "file": "README.md",
    "complexity": "low",
}
subs3 = td.decompose(task3)
assert_test("fallback_min_2", len(subs3) >= 2, f"got {len(subs3)}")
assert_test("fallback_max_4", len(subs3) <= 4, f"got {len(subs3)}")

# ── 4. LLM decomposition (mock) ────────────────────────────────────────
print("\n4. LLM decomposition — Mock caller")


def mock_llm(prompt: str) -> str:
    return """[
      {"action": "Step A", "file": "a.py", "complexity": "low"},
      {"action": "Step B", "file": "b.py", "complexity": "medium"}
    ]"""


td_llm = TaskDecomposer(llm_caller=mock_llm)
task4 = {
    "task_id": "t4",
    "action": "Refactor the entire auth system",
    "file": "auth.py",
    "complexity": "high",
}
subs4 = td_llm.decompose(task4)
assert_test("llm_count", len(subs4) == 2, f"got {len(subs4)}")
assert_test("llm_step_a", any("Step A" in s["action"] for s in subs4))
assert_test("llm_step_b", any("Step B" in s["action"] for s in subs4))
assert_test("llm_complexity_low", any(s["complexity"] == "low" for s in subs4))
assert_test("llm_parent", all(s.get("parent_task") == "t4" for s in subs4))

# ── 5. LLM fallback to heuristic on bad JSON ─────────────────────────
print("\n5. LLM fallback — Invalid JSON returns heuristic")


def bad_llm(prompt: str) -> str:
    return "This is not JSON at all"


td_bad = TaskDecomposer(llm_caller=bad_llm)
subs5 = td_bad.decompose(task4)
assert_test("fallback_on_bad_json", len(subs5) >= 2, f"got {len(subs5)}")

# ── 6. Depth tracking through multiple decompositions ────────────────
print("\n6. Depth tracking — Multiple decompositions")
task6 = {
    "task_id": "t6",
    "action": "Build authentication and integrate database and add tests",
    "file": "app.py",
    "complexity": "high",
    "decomposition_depth": 1,  # already decomposed once
}
subs6 = td.decompose(task6)
assert_test("depth_incremented", all(s.get("decomposition_depth") == 2 for s in subs6))

# ── 7. Sub-task IDs are unique and derived from parent ─────────────────
print("\n7. Sub-task ID uniqueness")
task7 = {
    "task_id": "auth_1",
    "action": "Create login and logout and register endpoints",
    "file": "auth.py",
    "complexity": "high",
}
subs7 = td.decompose(task7)
ids = [s["task_id"] for s in subs7]
assert_test("unique_ids", len(ids) == len(set(ids)), f"ids={ids}")
assert_test("derived_from_parent", all("auth_1" in s["task_id"] for s in subs7))

# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)

passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)

for name, ok, detail in results:
    mark = PASS if ok else FAIL
    print(f"  {mark} {name}")
    if not ok and detail:
        print(f"      → {detail}")

print()
print(f"Total: {passed}/{len(results)} passed  |  {failed} failed")

if failed > 0:
    print("\nSome tests FAILED.")
    sys.exit(1)
else:
    print("\nAll TaskDecomposer tests PASSED.")
    sys.exit(0)
