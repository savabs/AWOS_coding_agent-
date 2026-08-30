#!/usr/bin/env python3
"""Run AWOS with color-highlighted live output so you can watch each stage."""

import os
import re
import subprocess
import sys

CYAN = "\033[36m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
RED = "\033[31m"
MAGENTA = "\033[35m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

SKIP_RE = re.compile(
    r"linucb|tensorflow|FutureWarning|UserWarning|ConvergenceWarning|"
    r"critic\]|Unable to register|cuDNN|cuFFT|cuBLAS|TF-TRT"
)


def highlight(line: str) -> str:
    if "[PLANNER]" in line or "[PLAN REVIEW]" in line:
        return f"{CYAN}▶ PLAN{RESET}  {line}"
    if "[TASK " in line or re.search(r"Task \d+/\d+", line):
        return f"{YELLOW}▶ TASK{RESET}  {line}"
    if "[WORKER]" in line or "generating patch" in line or "Worker attempt" in line:
        return f"{MAGENTA}▶ WORK{RESET}  {line}"
    if "VERIFIED:" in line or "verification passed" in line or "task complete" in line:
        return f"{GREEN}✓ PASS{RESET}  {line}"
    if any(x in line for x in ("VERIFICATION FAILED", "verification failed", "task failed", "ERROR:")):
        return f"{RED}✗ FAIL{RESET}  {line}"
    if "[BREAKER]" in line or "Stagnation" in line:
        return f"{RED}⏸ PAUSE{RESET} {line}"
    if any(x in line for x in ("EXECUTION SUMMARY", "Tasks Completed:", "Success:")):
        return f"{BOLD}{line}{RESET}"
    if SKIP_RE.search(line):
        return ""
    return line


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: run_visible_awos.py \"<goal>\"")
        return 1

    goal = sys.argv[1]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(root)

    print()
    print(f"{BOLD}╔══════════════════════════════════════════════════════════════════════╗{RESET}")
    print(f"{BOLD}║  AWOS VISIBLE RUN — watch highlighted stages below                   ║{RESET}")
    print(f"{BOLD}╚══════════════════════════════════════════════════════════════════════╝{RESET}")
    print()
    print(f"{DIM}Goal:{RESET} {goal[:72]}{'...' if len(goal) > 72 else ''}")
    print()
    print(f"{DIM}Legend:{RESET} {CYAN}PLAN{RESET} · {YELLOW}TASK{RESET} · {MAGENTA}WORK{RESET} · {GREEN}PASS{RESET} · {RED}FAIL{RESET}")
    print(f"{DIM}{'─' * 72}{RESET}")
    print()

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        [sys.executable, "awos.py", "worker", "start", "--auto-approve", goal],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )

    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        out = highlight(line)
        if out:
            print(out, flush=True)

    code = proc.wait()
    print()
    print(f"{DIM}{'─' * 72}{RESET}")
    if code == 0:
        print(f"{GREEN}{BOLD}Run finished (exit 0){RESET}")
    else:
        print(f"{RED}{BOLD}Run finished (exit {code}){RESET}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
