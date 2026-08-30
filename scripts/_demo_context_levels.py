#!/usr/bin/env python3
"""Helper for demo_multi_resolution_context.sh — context level demo."""

import sys
from pathlib import Path

# Ensure scaffold/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scaffold.agent.multi_resolution_context import (
    MultiResolutionContextPipeline, suggest_level,
)


def main():
    pipeline = MultiResolutionContextPipeline()

    # Read real file
    try:
        content = Path("scaffold/agent/worker.py").read_text()
    except Exception:
        content = "def execute_task(): pass"

    task = {
        "action": "add input validation to execute_task to reject empty action strings",
        "file": "scaffold/agent/worker.py",
        "complexity": "medium",
    }

    full_file_tokens = len(content) // 4

    for level in range(4):
        a = pipeline.build(task, level=level, file_content=content)
        sections = []
        if a.task_section:
            sections.append("task")
        if a.module_section:
            sections.append("module")
        if a.signature_section:
            sections.append("sigs")
        if a.chunk_section:
            sections.append("chunks")
        if a.file_section:
            sections.append("file")
        pct = int((1 - a.estimated_tokens / max(1, full_file_tokens)) * 100)
        parts = ", ".join(sections)
        print(
            f"  Level {level}: {a.estimated_tokens:>5} tokens  "
            f"({pct:>3}% savings vs raw)  [{parts}]"
        )

    sl = suggest_level(task)
    print("")
    print(f"  suggest_level -> {sl} (complexity=medium -> expect 2)")


if __name__ == "__main__":
    main()
