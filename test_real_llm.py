#!/usr/bin/env python3
"""
Run AWOS Orchestrator with REAL LLM APIs on a temp file.
Safe: temp dir, small task, costs ~$0.01-0.02.
"""
import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, "scaffold")

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

os.environ.setdefault("AWOS_MONTHLY_BUDGET", "20.0")

from agent.orchestrator import Orchestrator


def main():
    with tempfile.TemporaryDirectory(prefix="awos_real_") as tmp_dir:
        # Create a tiny Python file
        demo = Path(tmp_dir) / "demo.py"
        demo.write_text("""# demo.py
# TODO: implement

def greet(name):
    return f"Hello, {name}!"
""")

        print("═" * 60)
        print("  AWOS REAL LLM TEST")
        print("  Temp dir:", tmp_dir)
        print("  Cost estimate: ~$0.01-0.02")
        print("═" * 60)
        print("\n  File BEFORE:")
        for line in demo.read_text().splitlines():
            print(f"    {line}")
        print()

        goal = "add type hints to the greet function in demo.py"

        orch = Orchestrator()
        result = orch.execute_feature(
            goal=goal,
            codebase_root=tmp_dir,
        )

        print("\n" + "═" * 60)
        print("  RESULT")
        print("═" * 60)
        print(f"  Success:     {result['success']}")
        print(f"  Tasks:       {result['tasks_completed']}/{result['total_tasks']}")
        print(f"  Failed:      {result['tasks_failed']}")
        print(f"  Time:        {result['time_elapsed']:.1f}s")

        print("\n  File AFTER:")
        for line in demo.read_text().splitlines():
            print(f"    {line}")

        print("\n" + "═" * 60)
        print("  Done. Temp dir will be cleaned up automatically.")
        print("═" * 60)


if __name__ == "__main__":
    main()
