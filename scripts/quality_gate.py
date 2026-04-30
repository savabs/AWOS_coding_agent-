#!/usr/bin/env python3
"""
quality_gate.py — Pre-completion quality gate for a task.

Checks:
  1. All task steps are marked done [x] (no pending [ ] or in-progress [~])
  2. Test suite passes (runs pytest or equivalent)
  3. Obsidian lint is clean (no FM01, FM02, LK01 errors)
  4. No Python errors in modified files (optional — requires pyright/mypy)
  5. Checkpoint file exists for today

Usage:
    python scripts/quality_gate.py --task tasks/active/my_feature.md
    python scripts/quality_gate.py --task tasks/active/my_feature.md --skip-tests
    python scripts/quality_gate.py --task tasks/active/my_feature.md --test-cmd "pytest tests/ -x -q"
"""

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path


def get_project_root() -> Path:
    here = Path.cwd()
    for parent in [here, *here.parents]:
        if (parent / ".git").exists() or (parent / "AWOS.md").exists():
            return parent
    return here


def check_task_steps(task_path: Path) -> tuple[bool, str]:
    """Verify all task steps are marked done."""
    content = task_path.read_text(encoding="utf-8")

    pending = re.findall(r"^\s*-\s*\[ \].*", content, re.MULTILINE)
    in_progress = re.findall(r"^\s*-\s*\[~\].*", content, re.MULTILINE)
    blocked = re.findall(r"^\s*-\s*\[!\].*", content, re.MULTILINE)
    done = re.findall(r"^\s*-\s*\[x\].*", content, re.MULTILINE)

    total = len(pending) + len(in_progress) + len(blocked) + len(done)
    incomplete = pending + in_progress

    if blocked:
        return False, (
            f"BLOCKED: {len(blocked)} blocked step(s). Resolve blockers before completing.\n"
            + "\n".join(f"  {s.strip()}" for s in blocked)
        )

    if incomplete:
        return False, (
            f"INCOMPLETE: {len(incomplete)} step(s) not done ({len(done)}/{total} complete)\n"
            + "\n".join(f"  {s.strip()}" for s in incomplete[:5])
            + ("\n  ..." if len(incomplete) > 5 else "")
        )

    if total == 0:
        return False, "No task steps found. Is this the right task file?"

    return True, f"Task steps: {len(done)}/{total} complete"


def run_tests(test_cmd: str, root: Path) -> tuple[bool, str]:
    """Run the test suite."""
    try:
        result = subprocess.run(
            test_cmd.split(),
            cwd=root,
            capture_output=True,
            text=True,
            timeout=300,
        )
        success = result.returncode == 0
        output = (result.stdout + result.stderr).strip()
        # Extract last few lines for summary
        lines = output.split("\n")
        summary = "\n".join(lines[-5:]) if len(lines) > 5 else output
        return success, summary
    except subprocess.TimeoutExpired:
        return False, "Tests timed out after 5 minutes"
    except FileNotFoundError:
        return False, f"Test command not found: {test_cmd.split()[0]}"


def run_obsidian_lint(root: Path) -> tuple[bool, str]:
    """Run obsidian lint and check for errors."""
    lint_script = root / "scripts" / "obsidian_lint.py"
    if not lint_script.exists():
        return True, "obsidian_lint.py not found — skipping"

    try:
        result = subprocess.run(
            [sys.executable, str(lint_script)],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=60,
        )
        success = result.returncode == 0
        output = (result.stdout + result.stderr).strip()
        return success, output
    except subprocess.TimeoutExpired:
        return False, "Lint timed out"


def check_checkpoint_exists(root: Path) -> tuple[bool, str]:
    """Check that a checkpoint was written today."""
    today = date.today().isoformat()
    memory_dir = root / "docs" / "memory"
    if not memory_dir.exists():
        return False, "docs/memory/ does not exist"

    checkpoints = list(memory_dir.glob(f"checkpoint_{today}*.md"))
    if checkpoints:
        return True, f"Checkpoint found: {checkpoints[0].name}"
    return (
        False,
        f"No checkpoint found for today ({today}). Write one with: python scripts/session_checkpoint.py",
    )


def check_structure_updated(root: Path) -> tuple[bool, str]:
    """Advisory check: remind to update structure file."""
    structure_file = root / "memories" / "repo" / "project_structure.md"
    if not structure_file.exists():
        return True, "memories/repo/project_structure.md not found — skipping"

    import datetime

    mtime = datetime.datetime.fromtimestamp(structure_file.stat().st_mtime)
    age_hours = (datetime.datetime.now() - mtime).total_seconds() / 3600

    if age_hours > 24:
        return False, (
            f"memories/repo/project_structure.md was last modified {age_hours:.0f}h ago. "
            "Update it with current test counts and phase progress."
        )
    return True, "memories/repo/project_structure.md updated recently"


def main():
    parser = argparse.ArgumentParser(description="Pre-completion quality gate.")
    parser.add_argument("--task", required=True, help="Path to active task file")
    parser.add_argument("--skip-tests", action="store_true", help="Skip running tests")
    parser.add_argument(
        "--test-cmd",
        default="pytest --tb=short -q",
        help="Test command to run (default: pytest --tb=short -q)",
    )
    args = parser.parse_args()

    root = get_project_root()
    task_path = Path(args.task)
    if not task_path.is_absolute():
        task_path = root / task_path

    if not task_path.exists():
        print(f"ERROR: Task file not found: {task_path}")
        sys.exit(1)

    print(f"Quality gate for: {task_path.relative_to(root)}")
    print("=" * 60)

    results = []

    # Check 1: Task steps
    ok, msg = check_task_steps(task_path)
    results.append(("PASS" if ok else "FAIL", "Task steps", msg))

    # Check 2: Tests
    if not args.skip_tests:
        ok, msg = run_tests(args.test_cmd, root)
        results.append(("PASS" if ok else "FAIL", "Tests", msg))
    else:
        results.append(("SKIP", "Tests", "skipped with --skip-tests"))

    # Check 3: Obsidian lint
    ok, msg = run_obsidian_lint(root)
    results.append(("PASS" if ok else "FAIL", "Obsidian lint", msg))

    # Check 4: Checkpoint
    ok, msg = check_checkpoint_exists(root)
    results.append(("PASS" if ok else "WARN", "Checkpoint", msg))

    # Check 5: Structure file (advisory)
    ok, msg = check_structure_updated(root)
    results.append(("PASS" if ok else "WARN", "Structure file", msg))

    # Report
    print()
    all_pass = True
    for status, check_name, msg in results:
        icon = {"PASS": "✓", "FAIL": "✗", "WARN": "⚠", "SKIP": "·"}.get(status, "?")
        print(f"  {icon} [{status}] {check_name}")
        if status in ("FAIL", "WARN") and msg:
            for line in msg.split("\n"):
                print(f"        {line}")
        elif status == "PASS" and msg and msg != check_name:
            print(f"        {msg}")
        if status == "FAIL":
            all_pass = False

    print()
    if all_pass:
        print("Gate PASSED. Task is ready to mark complete.")
        print(
            "Next steps:\n"
            "  1. Change task Status: to 'completed'\n"
            "  2. Change status/active tag to status/done\n"
            "  3. Move task file to tasks/done/\n"
            "  4. Write final checkpoint"
        )
        sys.exit(0)
    else:
        print("Gate FAILED. Fix the issues above before marking the task complete.")
        sys.exit(1)


if __name__ == "__main__":
    main()
