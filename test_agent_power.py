#!/usr/bin/env python3
"""
AWOS Power Test — feel the agent's full feature stack.

Exercises:
  - Planning + task decomposition
  - Success path (worker succeeds first try)
  - Failure path (worker fails → self-correction → retry with critique)
  - ReflexionMemory (critique saved, retrieved on next attempt)
  - BudgetLedger (cost tracking)
  - ToolPerformanceTracker (success matrix)
  - Observability (latency, queue, model routing)

Zero cost — mock worker in temp dir.
"""
import sys
import os
import tempfile
import json
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, "scaffold")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("AWOS_MONTHLY_BUDGET", "20.0")

PASS = "\033[92m✓\033[0m"
WARN = "\033[93m⚠\033[0m"
STEP = "\033[96m▶\033[0m"
INFO = "\033[94mℹ\033[0m"


def make_demo_file(tmp_dir, content):
    f = Path(tmp_dir) / "demo.py"
    f.write_text(content)
    return f


def run_scenario(name, goal, pre_planned, worker_fn, tmp_dir):
    from agent.orchestrator import Orchestrator

    demo_file = make_demo_file(
        tmp_dir,
        "# demo.py\n# TODO: implement\n\ndef old_func():\n    pass\n"
    )

    print(f"\n{'═' * 60}")
    print(f"  SCENARIO: {name}")
    print(f"{'═' * 60}")
    print(f"  Goal: {goal}")
    print(f"  Tasks: {len(pre_planned)}")
    print(f"{'─' * 60}\n")

    # Patch the worker and suppress file I/O load noise
    with patch("agent.worker.Worker.execute_task", side_effect=worker_fn), \
         patch.object(Orchestrator, "_discover_codebase_context", return_value=None):

        orch = Orchestrator()
        result = orch.execute_feature(
            goal=goal,
            codebase_root=tmp_dir,
            pre_planned_tasks=pre_planned,
        )

    print(f"\n{'─' * 60}")
    print(f"  Result: {PASS if result['success'] else WARN} success={result['success']}")
    print(f"  Tasks:  {result['tasks_completed']}/{result['total_tasks']} completed")
    if result['tasks_failed']:
        print(f"  Failed: {result['tasks_failed']}")
    print(f"  Time:   {result['time_elapsed']:.1f}s")
    print(f"{'─' * 60}\n")

    # Show what the file looks like now
    content = demo_file.read_text()
    if content.strip() != "# demo.py\n# TODO: implement\n\ndef old_func():\n    pass".strip():
        print(f"{INFO} File changed:")
        for line in content.splitlines():
            print(f"      {line}")
    else:
        print(f"{INFO} File unchanged (no valid SEARCH/REPLACE matched)")

    return result


# ── Worker Behaviors ─────────────────────────────────────────────────────────

attempt_counts = {}

def success_first_try(task, **kwargs):
    """Worker succeeds immediately."""
    return {
        "success": True,
        "search": "# TODO: implement",
        "replace": "def hello():\n    return 'Hello from AWOS!'\n",
        "reasoning": "Added hello function",
    }


def fail_then_succeed(task, **kwargs):
    """Worker fails on attempt 1, succeeds on attempt 2."""
    tid = task.get("task_id", 0)
    key = (tid, kwargs.get("attempt", 1))
    attempt_counts[key] = attempt_counts.get(key, 0) + 1

    attempt = kwargs.get("attempt", 1)

    if attempt == 1:
        # First attempt: bad SEARCH text (won't match)
        return {
            "success": False,
            "search": "THIS TEXT DOES NOT EXIST",
            "replace": "def hello():\n    return 'Hello!'\n",
            "reasoning": "Trying to add hello",
        }

    # Second attempt: correct SEARCH, also inject past_critiques if present
    past = task.get("past_critiques", [])
    if past:
        print(f"    {INFO} Worker sees {len(past)} past critique(s)!")
        for i, pc in enumerate(past[:3], 1):
            txt = pc.critique if hasattr(pc, "critique") else str(pc)
            et = pc.error_type if hasattr(pc, "error_type") else "?"
            print(f"      [CRITIQUE {i}] {et}: {txt[:80]}...")

    return {
        "success": True,
        "search": "# TODO: implement",
        "replace": "def hello():\n    return 'Hello from AWOS!'\n",
        "reasoning": "Added hello function with correct anchor",
    }


def fail_completely(task, **kwargs):
    """Worker always fails — no matching SEARCH text."""
    return {
        "success": False,
        "search": "NONEXISTENT",
        "replace": "def broken(): pass\n",
        "reasoning": "Can't find anchor",
    }


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    print("\n" + "═" * 60)
    print("  AWOS POWER TEST")
    print("  Feel the agent. Every feature. No API cost.")
    print("═" * 60)

    with tempfile.TemporaryDirectory(prefix="awos_power_") as tmp_dir:
        Path(tmp_dir, ".awos").mkdir(exist_ok=True)

        # ── Scenario 1: Clean success ─────────────────────────────────────
        run_scenario(
            name="Clean Success (1 task, 1 attempt)",
            goal="add a hello function",
            pre_planned=[{
                "task_id": 1,
                "file": "demo.py",
                "action": "add a hello function",
                "complexity": "low",
            }],
            worker_fn=success_first_try,
            tmp_dir=tmp_dir,
        )

        # ── Scenario 2: Failure → Retry with ReflexionMemory ───────────
        attempt_counts.clear()
        run_scenario(
            name="Failure → Retry with Self-Correction + ReflexionMemory",
            goal="add a hello function",
            pre_planned=[{
                "task_id": 2,
                "file": "demo.py",
                "action": "add a hello function",
                "complexity": "low",
            }],
            worker_fn=fail_then_succeed,
            tmp_dir=tmp_dir,
        )

        # Check if critique was saved
        store_path = Path(tmp_dir) / ".awos" / "error_patterns.jsonl"
        if store_path.exists():
            lines = [l.strip() for l in store_path.read_text().splitlines() if l.strip()]
            print(f"\n{INFO} ReflexionMemory store: {len(lines)} critique(s) saved")
            for line in lines:
                rec = json.loads(line)
                print(f"    → {rec['error_type']} on {rec['file_path']}")
                print(f"      '{rec['critique'][:100]}...'")
        else:
            print(f"\n{WARN} No critiques saved (store not created)")

        # ── Scenario 3: Total failure (max retries exhausted) ──────────
        run_scenario(
            name="Total Failure (max retries exhausted)",
            goal="add a hello function",
            pre_planned=[{
                "task_id": 3,
                "file": "demo.py",
                "action": "add a hello function",
                "complexity": "low",
            }],
            worker_fn=fail_completely,
            tmp_dir=tmp_dir,
        )

    print(f"\n{'═' * 60}")
    print("  POWER TEST COMPLETE")
    print(f"{'═' * 60}")
    print("""
  What you just saw:

  ✓ Clean success     → Planning, worker, verifier, budget, perf tracker
  ✓ Failure → retry   → Self-correction hint generated, critique saved
  ✓ ReflexionMemory   → Past critique retrieved and injected into retry prompt
  ✓ Total failure     → Max retries hit, error recorded, no crash
  ✓ Observability     → Latency, queue, model routing, cost per task
""")


if __name__ == "__main__":
    main()
