#!/usr/bin/env python3
"""
AWOS Demo Runner — feel the agent flow without API keys.
Uses pre-planned tasks + mock worker. Zero cost. Safe (temp dir).

Usage:
    python3 run_awos_demo.py

Type a goal like:
    "add a hello function to demo.py"
    "refactor the code to use classes"
"""
import sys
import os
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, "scaffold")
os.environ.setdefault("ANTHROPIC_API_KEY", "demo-key")
os.environ.setdefault("AWOS_MONTHLY_BUDGET", "20.0")

PASS = "\033[92m✓\033[0m"
INFO = "\033[94mℹ\033[0m"
STEP = "\033[96m▶\033[0m"


def make_demo_file(tmp_dir, content):
    f = Path(tmp_dir) / "demo.py"
    f.write_text(content)
    return f


def demo_banner():
    print("\n" + "═" * 60)
    print("  AWOS — DEMO MODE (no API keys needed, zero cost)")
    print("═" * 60)
    print("\n  This demo uses:")
    print(f"    {STEP} Pre-planned tasks (no Claude Planner)")
    print(f"    {STEP} Mock Worker responses (no DeepSeek/Claude API)")
    print(f"    {STEP} Real Verifier + real file I/O (in temp dir)")
    print(f"    {STEP} Full Orchestrator pipeline")
    print()


def get_mock_worker_for_goal(goal: str):
    """Return a mock worker function tailored to the goal."""
    goal_lower = goal.lower()

    if "hello" in goal_lower or "function" in goal_lower:
        def worker(task, **kwargs):
            return {
                "success": True,
                "search": "# TODO: implement",
                "replace": "def hello():\n    return 'Hello from AWOS!'\n",
                "explanation": "Added hello function",
            }
        return worker

    if "class" in goal_lower or "refactor" in goal_lower:
        def worker(task, **kwargs):
            return {
                "success": True,
                "search": "# TODO: implement",
                "replace": "class Greeter:\n    def greet(self, name):\n        return f'Hello, {name}!'\n",
                "explanation": "Refactored into a Greeter class",
            }
        return worker

    if "type" in goal_lower or "hint" in goal_lower:
        def worker(task, **kwargs):
            return {
                "success": True,
                "search": "# TODO: implement",
                "replace": "def greet(name: str) -> str:\n    return f'Hello, {name}!'\n",
                "explanation": "Added greet function with type hints",
            }
        return worker

    # Default: add a print statement
    def worker(task, **kwargs):
        return {
            "success": True,
            "search": "# TODO: implement",
            "replace": "print('AWOS was here!')\n",
            "explanation": "Added a print statement",
        }
    return worker


def run_demo(goal: str):
    from agent.orchestrator import Orchestrator

    with tempfile.TemporaryDirectory(prefix="awos_demo_") as tmp_dir:
        # Create a demo file
        demo_file = make_demo_file(tmp_dir, "# demo.py\n# TODO: implement\n")

        # Build pre-planned task
        pre_planned = [{
            "task_id": 1,
            "file": "demo.py",
            "action": goal,
            "complexity": "low",
        }]

        mock_worker = get_mock_worker_for_goal(goal)

        print(f"{'─' * 60}")
        print(f"  GOAL: {goal}")
        print(f"{'─' * 60}\n")

        print(f"{STEP} Starting Orchestrator...")
        print(f"{INFO} Working directory: {tmp_dir}")
        print(f"{INFO} Source file before:")
        for line in demo_file.read_text().splitlines():
            print(f"      {line}")
        print()

        with patch("agent.worker.Worker.execute_task", side_effect=mock_worker), \
             patch("agent.core.performance_tracker.ToolPerformanceTracker._load"):

            orch = Orchestrator()
            result = orch.execute_feature(
                goal=goal,
                codebase_root=tmp_dir,
                pre_planned_tasks=pre_planned,
            )

        print(f"\n{'=' * 60}")
        print(f"  RESULT")
        print(f"{'=' * 60}")
        print(f"  Success:     {PASS} {result['success']}")
        print(f"  Tasks:       {result['tasks_completed']}/{result['total_tasks']} completed")
        if result['tasks_failed']:
            print(f"  Failed:      {result['tasks_failed']}")
        print(f"  Time:        {result['time_elapsed']:.1f}s")

        print(f"\n{INFO} Source file after:")
        for line in demo_file.read_text().splitlines():
            print(f"      {line}")

        print(f"\n{PASS} Demo complete. Files were in a temp dir — nothing changed in your project.")


def main():
    demo_banner()

    print("  Type your goal below, or pick a preset:")
    print("    1 → add a hello function")
    print("    2 → refactor to use classes")
    print("    3 → add type hints")
    print("  Or type 'quit' to exit.")
    print("─" * 60)

    while True:
        try:
            print()
            goal = input("🎯 Goal: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not goal or goal.lower() in ("quit", "exit", "q"):
            print("Goodbye!")
            break

        # Map presets
        presets = {
            "1": "add a hello function",
            "2": "refactor to use classes",
            "3": "add type hints",
        }
        goal = presets.get(goal, goal)

        run_demo(goal)

        print(f"\n{'─' * 60}")
        print("  Done. Type another goal, or 'quit' to exit.")
        print(f"{'─' * 60}")


if __name__ == "__main__":
    main()
