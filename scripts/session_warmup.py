#!/usr/bin/env python3
"""
session_warmup.py — Print a context brief for the agent at session start.

Outputs:
  - Latest checkpoint summary
  - Active tasks with step counts (done vs pending)
  - Last 5 git commits
  - Project structure snapshot
  - Available prompts

Usage:
    python scripts/session_warmup.py
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _root() -> Path:
    """Project root is one level above scripts/."""
    return Path(__file__).resolve().parent.parent


def _latest_checkpoint(memory_dir: Path) -> str:
    checkpoints = sorted(memory_dir.glob("checkpoint_*.md"), reverse=True)
    if not checkpoints:
        return "  (no checkpoints yet)"
    cp = checkpoints[0]
    lines = cp.read_text(encoding="utf-8").splitlines()
    title = cp.stem
    in_fm = False
    fm_done = False
    sections: list[str] = []
    for line in lines:
        if line.strip() == "---" and not fm_done:
            if in_fm:
                fm_done = True
            in_fm = not in_fm
            continue
        if in_fm and line.startswith("title:"):
            title = line.replace("title:", "").strip().strip('"')
        if fm_done and line.startswith("## "):
            sections.append(line[3:].strip())
        if len(sections) >= 4:
            break
    sections_str = ", ".join(sections) if sections else "—"
    return f"  File: {cp.name}\n  Title: {title}\n  Sections: {sections_str}"


def _active_tasks(tasks_dir: Path) -> list[str]:
    if not tasks_dir.exists():
        return ["  (tasks/active/ not found)"]
    tasks = sorted(tasks_dir.glob("*.md"))
    if not tasks:
        return ["  (no active tasks)"]
    results = []
    for t in tasks:
        text = t.read_text(encoding="utf-8")
        done = text.count("- [x]") + text.count("- [X]")
        pending = text.count("- [ ]")
        # Find next pending step description
        next_step = ""
        for line in text.splitlines():
            if line.strip().startswith("- [ ]"):
                next_step = line.strip()[6:].strip()[:70]
                break
        results.append(
            f"  {t.name}\n"
            f"    Progress: {done} done / {pending} pending\n"
            f"    Next:     {next_step or '(no unchecked steps found)'}"
        )
    return results


def _git_log(root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "-5"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        lines = result.stdout.strip().splitlines()
        return [f"  {l}" for l in lines] if lines else ["  (no commits)"]
    except Exception:
        return ["  (git unavailable)"]


def _structure_snapshot(root: Path) -> list[str]:
    structure_file = root / "memories" / "repo" / "project_structure.md"
    if not structure_file.exists():
        return ["  (memories/repo/project_structure.md not found — create it)"]
    lines = structure_file.read_text(encoding="utf-8").splitlines()
    preview = [l for l in lines if l.strip() and not l.startswith("---")][:15]
    return [f"  {l}" for l in preview]


def _available_prompts(root: Path) -> list[str]:
    prompts_dir = root / "prompts"
    if not prompts_dir.exists():
        return ["  (no prompts/ directory)"]
    prompts = sorted(prompts_dir.glob("*.prompt.md"))
    if not prompts:
        return ["  (no .prompt.md files)"]
    results = []
    for p in prompts:
        # Read description from frontmatter
        desc = ""
        in_fm = False
        for line in p.read_text(encoding="utf-8").splitlines()[:15]:
            if line.strip() == "---":
                in_fm = not in_fm
                continue
            if in_fm and line.startswith("description:"):
                desc = line.replace("description:", "").strip().strip('"').strip("'")
                break
        results.append(f"  @{p.stem:<30}  {desc[:60]}")
    return results


def main() -> None:
    root = _root()
    memory_dir = root / "docs" / "memory"
    tasks_dir = root / "tasks" / "active"

    sep = "=" * 62

    print(sep)
    print("  AGENTIC OS — SESSION WARMUP")
    print(sep)

    print("\n[LATEST CHECKPOINT]")
    print(_latest_checkpoint(memory_dir))

    print("\n[ACTIVE TASKS]")
    for line in _active_tasks(tasks_dir):
        print(line)

    print("\n[RECENT COMMITS]")
    for line in _git_log(root):
        print(line)

    print("\n[PROJECT STRUCTURE SNAPSHOT]")
    for line in _structure_snapshot(root):
        print(line)

    print("\n[AVAILABLE PROMPTS]")
    for line in _available_prompts(root):
        print(line)

    print(f"\n{sep}")
    print("  Cold-start complete.")
    print("  Next: read tasks/active/ + latest checkpoint, then declare state.")
    print(sep)


if __name__ == "__main__":
    main()
