#!/usr/bin/env python3
"""Helper for demo_multi_resolution_context.sh — edge cases demo."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scaffold.agent.multi_resolution_context import (
    MultiResolutionContextPipeline, suggest_level,
)


def main():
    pipeline = MultiResolutionContextPipeline()

    # Edge: missing file
    a = pipeline.build(
        {"action": "fix typo", "file": "does_not_exist.py"}, level=3
    )
    print(f"  Missing file:       Level {a.level}, {a.estimated_tokens} tokens (no crash)")

    # Edge: empty task
    a = pipeline.build({}, level=0)
    print(f'  Empty task:         Level {a.level}, task="TASK: " (no crash)')

    # Edge: level clamp
    a = pipeline.build({"action": "test"}, level=99)
    print(f"  Level 99 clamped:   Level {a.level} (expect 3)")
    a = pipeline.build({"action": "test"}, level=-5)
    print(f"  Level -5 clamped:   Level {a.level} (expect 0)")

    # suggest_level edge cases
    tests = [
        ({"action": "fix typo in variable name"}, 0),
        ({"complexity": "low", "action": "add a simple test"}, 1),
        ({"complexity": "medium", "action": "refactor module"}, 2),
        ({"complexity": "high", "action": "redesign auth module"}, 3),
    ]
    for t, expected in tests:
        result = suggest_level(t)
        status = "PASS" if result == expected else "FAIL"
        action = t["action"][:30]
        print(f"  [{status}] suggest_level({action:30}) -> {result} (expect {expected})")


if __name__ == "__main__":
    main()
