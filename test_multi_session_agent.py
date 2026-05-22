#!/usr/bin/env python3
"""
Smoke test for AgentStateManager — persistence across sessions.
No API keys required.
"""
import sys
import os
import tempfile

sys.path.insert(0, "scaffold")
sys.path.insert(0, "scaffold/agent")
os.environ.setdefault("AWOS_MONTHLY_BUDGET", "20.0")

from agent_state_manager import AgentStateManager

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results = []


def check(name, cond, detail=""):
    results.append((name, cond, detail))
    mark = PASS if cond else FAIL
    extra = f" — {detail}" if not cond and detail else ""
    print(f"  {mark} {name}{extra}")


print("=" * 60)
print("Multi-Session Agent Smoke Test")
print("=" * 60)

with tempfile.TemporaryDirectory() as tmp:
    mgr = AgentStateManager(state_dir=tmp)

    # ── 1. load returns empty state for new goal ────────────────────────
    print("\n1. Empty state for new goal")
    state = mgr.load("build login system")
    check("status_in_progress", state["status"] == "in_progress")
    check("completed_empty", state["completed_task_ids"] == [])
    check("failed_empty", state["failed_task_ids"] == [])
    check("goal_preserved", state["goal"] == "build login system")
    check("goal_hash_set", len(state["goal_hash"]) == 10)

    # ── 2. mark_complete persists ─────────────────────────────────────
    print("\n2. mark_complete")
    mgr.mark_complete(state, task_id=1)
    state2 = mgr.load("build login system")
    check("task1_completed", 1 in state2["completed_task_ids"])
    check("task1_not_failed", 1 not in state2["failed_task_ids"])

    # ── 3. mark_failed persists ──────────────────────────────────────
    print("\n3. mark_failed")
    mgr.mark_failed(state2, task_id=2)
    state3 = mgr.load("build login system")
    check("task2_failed", 2 in state3["failed_task_ids"])
    check("task1_still_complete", 1 in state3["completed_task_ids"])

    # ── 4. re-mark_complete removes from failed ──────────────────────
    print("\n4. Re-mark complete removes from failed")
    mgr.mark_complete(state3, task_id=2)
    state4 = mgr.load("build login system")
    check("task2_now_complete", 2 in state4["completed_task_ids"])
    check("task2_not_failed", 2 not in state4["failed_task_ids"])

    # ── 5. finalize sets status ───────────────────────────────────────
    print("\n5. finalize")
    mgr.finalize(state4, success=True)
    state5 = mgr.load("build login system")
    check("status_complete", state5["status"] == "complete")

    mgr.finalize(state5, success=False)
    state6 = mgr.load("build login system")
    check("status_failed", state6["status"] == "failed")

    # ── 6. different goals = different files ──────────────────────────
    print("\n6. Isolation between goals")
    state_b = mgr.load("add search feature")
    check("b_empty", state_b["completed_task_ids"] == [])
    mgr.mark_complete(state_b, task_id=99)
    state_a = mgr.load("build login system")
    check("a_unchanged", 99 not in state_a["completed_task_ids"])
    check("b_has_99", 99 in state_b["completed_task_ids"])

    # ── 7. list_goals returns both ────────────────────────────────────
    print("\n7. list_goals")
    goals = mgr.list_goals()
    check("two_goals", len(goals) == 2, f"got {len(goals)}")
    goal_names = {g["goal"] for g in goals}
    check("both_names", goal_names == {"build login system", "add search feature"}, f"got {goal_names}")

    # ── 8. add_session records session_id ─────────────────────────────
    print("\n8. add_session")
    state7 = mgr.load("build login system")
    mgr.add_session(state7, "sess_abc123")
    state8 = mgr.load("build login system")
    check("session_recorded", "sess_abc123" in state8["session_ids"])

    # ── 9. hash is consistent (same goal → same file) ───────────────
    print("\n9. Hash consistency")
    state9a = mgr.load("  Build LOGIN System  ")
    state9b = mgr.load("build login system")
    check("same_hash", state9a["goal_hash"] == state9b["goal_hash"])

# ═══════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
failed_count = sum(1 for _, ok, _ in results if not ok)
print(f"Total: {passed}/{len(results)} passed  |  {failed_count} failed")
if failed_count:
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL {name}: {detail}")
    sys.exit(1)
else:
    print("\nAll Multi-Session Agent tests PASSED.")
    sys.exit(0)
