#!/usr/bin/env python3
"""
session_checkpoint.py — Auto-generate a session checkpoint from git state and active tasks.

Usage:
    python scripts/session_checkpoint.py [-m "summary of session"]
    python scripts/session_checkpoint.py --dry-run

Output: docs/memory/checkpoint_YYYY-MM-DD[_N].md

The script produces a DRAFT. Review and edit before considering it final.
The draft includes:
  - Git changed files since last commit
  - Recent commit messages
  - Active task file status (steps done vs pending)
  - Prompted summary from -m argument or stdin
"""

import argparse
import datetime
import re
import subprocess
from pathlib import Path


def get_project_root() -> Path:
    """Find project root by looking for .git or AWOS.md."""
    here = Path.cwd()
    for parent in [here, *here.parents]:
        if (parent / ".git").exists() or (parent / "AWOS.md").exists():
            return parent
    return here


def run_git(args: list[str], cwd: Path) -> str:
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def get_changed_files(root: Path) -> list[str]:
    """Get files changed since last commit (staged + unstaged)."""
    staged = run_git(["diff", "--name-status", "--cached"], root)
    unstaged = run_git(["diff", "--name-status"], root)
    untracked = run_git(["ls-files", "--others", "--exclude-standard"], root)

    lines = []
    for line in (staged + "\n" + unstaged).strip().split("\n"):
        line = line.strip()
        if line and not line.startswith("?"):
            lines.append(line)
    for f in untracked.strip().split("\n"):
        if f.strip():
            lines.append(f"U\t{f.strip()}")

    # Deduplicate
    seen = set()
    result = []
    for line in lines:
        if line not in seen:
            seen.add(line)
            result.append(line)
    return result


def get_recent_commits(root: Path, n: int = 5) -> list[str]:
    log = run_git(["log", f"-{n}", "--oneline", "--no-merges"], root)
    return log.split("\n") if log else []


def get_active_tasks(root: Path) -> list[dict]:
    tasks_dir = root / "tasks" / "active"
    if not tasks_dir.exists():
        return []

    tasks = []
    for task_file in sorted(tasks_dir.glob("*.md")):
        if task_file.name.startswith("TASK_TEMPLATE"):
            continue
        content = task_file.read_text(encoding="utf-8")

        # Count steps
        done = len(re.findall(r"^\s*-\s*\[x\]", content, re.MULTILINE))
        in_progress = len(re.findall(r"^\s*-\s*\[~\]", content, re.MULTILINE))
        blocked = len(re.findall(r"^\s*-\s*\[!\]", content, re.MULTILINE))
        pending = len(re.findall(r"^\s*-\s*\[ \]", content, re.MULTILINE))
        total = done + in_progress + blocked + pending

        # Find next pending step
        next_step = None
        for line in content.split("\n"):
            if re.match(r"^\s*-\s*\[[ ~]\]", line):
                next_step = (
                    line.strip()
                    .lstrip("-")
                    .lstrip()
                    .lstrip("[~] ")
                    .lstrip("[ ] ")
                    .strip()
                )
                break

        tasks.append(
            {
                "name": task_file.stem,
                "path": str(task_file.relative_to(root)),
                "done": done,
                "total": total,
                "next_step": next_step,
            }
        )
    return tasks


def find_latest_checkpoint(root: Path) -> str | None:
    memory_dir = root / "docs" / "memory"
    if not memory_dir.exists():
        return None
    checkpoints = sorted(
        [f for f in memory_dir.glob("checkpoint_*.md") if "TEMPLATE" not in f.name],
        reverse=True,
    )
    if checkpoints:
        return checkpoints[0].stem
    return None


def generate_checkpoint_path(root: Path) -> Path:
    today = datetime.date.today().isoformat()
    memory_dir = root / "docs" / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    base = memory_dir / f"checkpoint_{today}.md"
    if not base.exists():
        return base

    # Add suffix if today's checkpoint already exists
    for i in range(2, 20):
        path = memory_dir / f"checkpoint_{today}_{i}.md"
        if not path.exists():
            return path
    return base  # fallback


def build_checkpoint_content(
    summary: str,
    changed_files: list[str],
    recent_commits: list[str],
    active_tasks: list[dict],
    latest_checkpoint: str | None,
    today: str,
) -> str:
    # Format changed files
    file_lines = (
        "\n".join(f"  {line}" for line in changed_files)
        if changed_files
        else "  (no changes detected)"
    )

    # Format commits
    commit_lines = (
        "\n".join(f"  {c}" for c in recent_commits)
        if recent_commits
        else "  (no commits)"
    )

    # Format tasks
    task_lines = []
    for t in active_tasks:
        pct = f"{t['done']}/{t['total']}" if t["total"] > 0 else "0/0"
        next_info = f" → next: {t['next_step']}" if t["next_step"] else ""
        task_lines.append(f"- [[{t['name']}]] ({pct} steps done){next_info}")
    task_section = "\n".join(task_lines) if task_lines else "- (no active tasks)"

    prior_link = f"[[{latest_checkpoint}]]" if latest_checkpoint else "(none)"

    return f"""---
title: "Checkpoint: {today}"
tags:
  - doc/checkpoint
  - phase/N
---

# Session Checkpoint — {today}

> **This is an immutable historical record.**
> Do not edit this file after the session ends.
> Corrections go to the canonical owner file and the NEXT checkpoint.

---

## Session Summary

{summary if summary else "(TODO: describe what was accomplished this session)"}

---

## Active Tasks

{task_section}

---

## Files Changed This Session

```
{file_lines}
```

---

## Recent Commits

```
{commit_lines}
```

---

## State at Session End

**Prior checkpoint:** {prior_link}
**Test count:** (TODO: update from `memories/repo/project_structure.md`)
**Phase status:** (TODO: update)

---

## What's Blocked

| Blocker | Task step | What's needed |
|---|---|---|
| (TODO) | — | — |

---

## Next Session Starting Point

1. Read `memories/repo/project_structure.md`
2. Read this checkpoint
3. (TODO: list active task file) — pick up at (TODO: step)

---

## Decisions Made This Session

| Decision | Written to | Rationale |
|---|---|---|
| (TODO or —) | — | — |

---

## Related

{chr(10).join(f"- [[{t['name']}]]" for t in active_tasks) if active_tasks else "- (none)"}
"""


def main():
    parser = argparse.ArgumentParser(description="Generate a session checkpoint draft.")
    parser.add_argument("-m", "--message", default="", help="Session summary message")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print to stdout, don't write file"
    )
    args = parser.parse_args()

    root = get_project_root()
    today = datetime.date.today().isoformat()

    changed_files = get_changed_files(root)
    recent_commits = get_recent_commits(root)
    active_tasks = get_active_tasks(root)
    latest_checkpoint = find_latest_checkpoint(root)

    content = build_checkpoint_content(
        summary=args.message,
        changed_files=changed_files,
        recent_commits=recent_commits,
        active_tasks=active_tasks,
        latest_checkpoint=latest_checkpoint,
        today=today,
    )

    if args.dry_run:
        print(content)
        return

    output_path = generate_checkpoint_path(root)
    output_path.write_text(content, encoding="utf-8")
    print(f"Checkpoint draft written to: {output_path.relative_to(root)}")
    print("Review and edit the TODO sections before considering it final.")


if __name__ == "__main__":
    main()
