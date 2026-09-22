#!/usr/bin/env python3
"""
Prepare clawcode for CI Rescue: create buggy branch + mission plan.

Usage:
    python3 scripts/clawcode_ci_rescue_prepare.py
    python3 scripts/clawcode_ci_rescue_prepare.py --restore   # back to main, delete branch
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLAWCODE = Path(os.environ.get("CLAWCODE_ROOT", "/home/becmachlean/2024/projects/clawcode"))
BRANCH = "awos-ci-rescue-buggy"
PLAN_OUT = ROOT / "docs" / "missions" / "clawcode_ci_rescue.plan.json"

# Each bug: file (under clawcode), task description, replace once (old must exist on main)
BUGS: list[dict] = [
    {
        "task_id": 1,
        "file": "src/port_manifest.py",
        "action": "Fix build_port_manifest: set total_python_files=len(files), not the hardcoded 5",
        "old": "total_python_files=len(files)",
        "new": "total_python_files=5",
        "complexity": "low",
    },
    {
        "task_id": 2,
        "file": "src/query_engine.py",
        "action": "Fix render_summary title: use '# Python Porting Workspace Summary' not 'Broken'",
        "old": "# Python Porting Workspace Summary",
        "new": "# Broken Workspace Summary",
        "complexity": "low",
    },
    {
        "task_id": 3,
        "file": "src/commands.py",
        "action": "Fix PORTED_COMMANDS: use full load_command_snapshot(), not sliced [:20]",
        "old": "PORTED_COMMANDS = load_command_snapshot()",
        "new": "PORTED_COMMANDS = load_command_snapshot()[:20]",
        "complexity": "medium",
    },
    {
        "task_id": 4,
        "file": "src/tools.py",
        "action": "Fix PORTED_TOOLS: use full load_tool_snapshot(), not sliced [:20]",
        "old": "PORTED_TOOLS = load_tool_snapshot()",
        "new": "PORTED_TOOLS = load_tool_snapshot()[:20]",
        "complexity": "medium",
    },
    {
        "task_id": 5,
        "file": "src/assistant/__init__.py",
        "action": "Fix assistant MODULE_COUNT: use _SNAPSHOT['module_count'], not 0",
        "old": "MODULE_COUNT = _SNAPSHOT['module_count']",
        "new": "MODULE_COUNT = 0",
        "complexity": "low",
    },
    {
        "task_id": 6,
        "file": "src/bridge/__init__.py",
        "action": "Fix bridge MODULE_COUNT: use _SNAPSHOT['module_count'], not 0",
        "old": "MODULE_COUNT = _SNAPSHOT['module_count']",
        "new": "MODULE_COUNT = 0",
        "complexity": "low",
    },
    {
        "task_id": 7,
        "file": "src/utils/__init__.py",
        "action": "Fix utils MODULE_COUNT: restore real snapshot module_count (must be >100)",
        "old": "MODULE_COUNT = _SNAPSHOT['module_count']",
        "new": "MODULE_COUNT = 10",
        "complexity": "low",
    },
    {
        "task_id": 8,
        "file": "src/deferred_init.py",
        "action": "Fix run_deferred_init: plugin_init should be enabled when trusted=True",
        "old": "plugin_init=enabled",
        "new": "plugin_init=False",
        "complexity": "low",
    },
    {
        "task_id": 9,
        "file": "src/setup.py",
        "action": "Fix setup report: heading must be 'Deferred init:' not 'Deferred startup:'",
        "old": "'Deferred init:'",
        "new": "'Deferred startup:'",
        "complexity": "low",
    },
    {
        "task_id": 10,
        "file": "src/remote_runtime.py",
        "action": "Fix run_remote_mode: return mode 'remote' not 'broken'",
        "old": "return RuntimeModeReport('remote', True,",
        "new": "return RuntimeModeReport('broken', True,",
        "complexity": "low",
    },
    {
        "task_id": 11,
        "file": "src/direct_modes.py",
        "action": "Fix run_direct_connect: return mode 'direct-connect' not 'broken-direct'",
        "old": "return DirectModeReport(mode='direct-connect', target=target, active=True)",
        "new": "return DirectModeReport(mode='broken-direct', target=target, active=True)",
        "complexity": "low",
    },
    {
        "task_id": 12,
        "file": "src/query_engine.py",
        "action": "Fix submit_message output: first summary line must start with 'Prompt:'",
        "old": "f'Prompt: {prompt}'",
        "new": "f'User prompt: {prompt}'",
        "complexity": "low",
    },
]

def _run(cmd: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)


def _apply_bugs() -> None:
    for bug in BUGS:
        path = CLAWCODE / bug["file"]
        text = path.read_text(encoding="utf-8")
        if bug["old"] not in text:
            print(f"ERROR: bug {bug['task_id']} old text not found in {bug['file']}", file=sys.stderr)
            raise SystemExit(1)
        path.write_text(text.replace(bug["old"], bug["new"], 1), encoding="utf-8")


def _restore_bugs() -> None:
    for bug in BUGS:
        path = CLAWCODE / bug["file"]
        text = path.read_text(encoding="utf-8")
        if bug["new"] in text:
            path.write_text(text.replace(bug["new"], bug["old"], 1), encoding="utf-8")


def _pytest_summary() -> tuple[int, int, int]:
    r = _run([sys.executable, "-m", "pytest", "tests/", "-q", "--tb=no"], CLAWCODE, check=False)
    out = r.stdout + r.stderr
    failed = passed = 0
    m = re.search(r"(\d+) failed.*?(\d+) passed", out)
    if m:
        failed, passed = int(m.group(1)), int(m.group(2))
    elif "passed" in out:
        m2 = re.search(r"(\d+) passed", out)
        passed = int(m2.group(1)) if m2 else 0
    return r.returncode, failed, passed


def _write_plan() -> None:
    plan = [
        {
            "task_id": b["task_id"],
            "task_type": "edit_file",
            "path": b["file"],
            "file": b["file"],
            "action": b["action"],
            "complexity": b.get("complexity", "low"),
        }
        for b in BUGS
    ]
    PLAN_OUT.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")


def prepare() -> None:
    if not CLAWCODE.is_dir():
        print(f"clawcode not found: {CLAWCODE}", file=sys.stderr)
        raise SystemExit(1)

    _run(["git", "checkout", "main"], CLAWCODE)
    _run(["git", "branch", "-D", BRANCH], CLAWCODE, check=False)
    _run(["git", "checkout", "-b", BRANCH], CLAWCODE)

    _apply_bugs()
    _run(["git", "add", "src/"], CLAWCODE)
    _run(["git", "commit", "-m", "awos: inject CI rescue bugs (12 sites)"], CLAWCODE)

    code, failed, passed = _pytest_summary()
    _write_plan()

    print(f"CLAWCODE_PREP: branch={BRANCH}")
    print(f"CLAWCODE_PREP: red_count={failed} green_count={passed}")
    print(f"CLAWCODE_PREP: plan_written={PLAN_OUT}")
    if failed < 8:
        print("CLAWCODE_PREP: WARNING — fewer than 8 failures; may be too easy", file=sys.stderr)
    if code == 0:
        print("CLAWCODE_PREP: ERROR — expected some failures", file=sys.stderr)
        raise SystemExit(1)


def restore() -> None:
    _run(["git", "checkout", "main"], CLAWCODE)
    _run(["git", "branch", "-D", BRANCH], CLAWCODE, check=False)
    print("CLAWCODE_PREP: restored main, deleted buggy branch")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--restore", action="store_true")
    args = parser.parse_args()
    if args.restore:
        restore()
    else:
        prepare()


if __name__ == "__main__":
    main()
