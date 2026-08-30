#!/usr/bin/env python3
"""Dummy Customer Simulation — AWOS Dogfood.

Simulates a paying customer ("DevMint Labs") using AWOS over ~2 weeks
on their codebase. Runs 10 realistic jobs, tracks cost per job,
proves compounding: Job 1 expensive -> Job 10 cheaper.

Usage:  python3 scripts/run_dummy_customer.py
"""

import csv
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env():
    """Load .env file into os.environ (handles special chars properly)."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        val = val.strip().strip('"').strip("'")
        os.environ[key] = val


JOBS = [
    ("EASY", "Add a single-line comment at the very top of scaffold/agent/verifier.py describing its role"),
    ("EASY", "Change the docstring in scaffold/agent/core/__init__.py from the placeholder to Core kernel modules"),
    ("EASY", "Replace the placeholder docstring in scaffold/agent/memory/__init__.py with Memory subsystem for persistence and retrieval"),
    ("EASY", "Replace the placeholder docstring in scaffold/agent/tools/__init__.py with Tool framework for agent tool definitions"),
    ("EASY", "Replace the placeholder docstring in scaffold/agent/config/__init__.py with Configuration subsystem"),
    ("EASY", "Add a one-line comment above the __init__ method with only pass in scaffold/agent/verifier.py explaining it is intentionally a no-op"),
    ("MEDIUM", "Add a docstring to the DiffHunk class in scaffold/agent/diff_builder.py explaining it models a unified diff hunk"),
    ("MEDIUM", "Rename the local variable color to status_color in the show_status method of scaffold/agent/budget_ledger.py"),
    ("MEDIUM", "Add docstrings to _check_cpp_syntax and _check_ts_syntax in scaffold/agent/verifier.py explaining what they validate"),
    ("MEDIUM", "In scaffold/agent/search_replace_parser.py add a module-level comment at the top saying it parses SEARCH/REPLACE blocks"),
    ("MEDIUM", "Change the debug print statement in cache_telemetry.py to use logger.debug instead of print if possible, otherwise just add a comment"),
]


def run_job(goal: str, job_num: int, total_jobs: int) -> dict:
    """Run a single job via awos worker start, streaming output in real-time."""
    print(f"\n{'=' * 65}")
    print(f"  JOB {job_num}/{total_jobs}  |  {goal[:78]}{'...' if len(goal) > 78 else ''}")
    print(f"{'=' * 65}", flush=True)

    start_ts = time.time()
    env = os.environ.copy()
    env.setdefault("AWOS_USE_WORKTREE", "true")
    env.setdefault("AWOS_RUNTIME_SESSION", "true")
    env.setdefault("AWOS_CHEAP_ONLY", "true")

    p = subprocess.Popen(
        [sys.executable, str(ROOT / "awos.py"), "worker", "start",
         "--root", str(ROOT), "--auto-approve", goal],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, env=env, cwd=str(ROOT),
        text=True, bufsize=1,
    )

    output_lines = []
    try:
        for line in p.stdout:
            output_lines.append(line)
            stripped = line.rstrip()
            if stripped:
                print(f"  {stripped[:120]}", flush=True)
        p.wait(timeout=300)
    except subprocess.TimeoutExpired:
        p.kill()
        print("  ⚠ TIMEOUT after 300s", flush=True)
        return {"status": "TIMEOUT", "tasks_completed": 0, "total_tasks": 0,
                "cost_usd": 0, "wall_sec": 300, "session_id": ""}

    wall_sec = time.time() - start_ts
    output = "".join(output_lines)

    status = "PASS" if ("Success:" in output and "✓" in output) else "FAIL"

    tasks_completed, total_tasks = 0, 0
    for line in output.splitlines():
        if line.strip().startswith("Tasks:"):
            parts = line.strip().split()
            if len(parts) >= 2:
                try:
                    tc, tt = parts[1].split("/")
                    tasks_completed = int(tc)
                    total_tasks = int(tt)
                except (ValueError, IndexError):
                    pass
            break

    session_id = ""
    for line in output.splitlines():
        if "Session:" in line and "rs_" in line:
            session_id = line.strip().split()[-1]
            break

    cost_usd = 0.0
    for line in output.splitlines():
        if "Total Cost:" in line:
            try:
                cost_usd = float(line.split("$")[-1].strip())
            except (ValueError, IndexError):
                pass

    print(f"\n  -> {status}  |  {tasks_completed}/{total_tasks} tasks  |  ${cost_usd:.4f}  |  {wall_sec:.0f}s", flush=True)
    return {"status": status, "tasks_completed": tasks_completed,
            "total_tasks": total_tasks, "cost_usd": cost_usd,
            "wall_sec": wall_sec, "session_id": session_id}


def main():
    load_env()

    if not os.getenv("ANTHROPIC_API_KEY") and not os.getenv("DEEPSEEK_API_KEY"):
        print("ERROR: No API keys found in .env")
        sys.exit(1)

    print("=" * 66)
    print("  AWOS DUMMY CUSTOMER SIMULATION")
    print("=" * 66)
    print("  Customer:  DevMint Labs (3-devs, Python microservices)")
    print("  Current:   Cursor Pro, want compounding savings")
    print("  Pilot:     10 jobs across 2 weeks on their codebase")
    print("=" * 66)
    print("  Mode:      CHEAP_ONLY=1  WORKTREE=1  RUNTIME_SESSION=1")
    print("  Workers:   DeepSeek V4 Flash  |  Planner: Gemini Flash")
    print(f"={'' * 65}")

    print(f"\nStarting {len(JOBS)} jobs...\n")

    results = []
    for i, (difficulty, goal) in enumerate(JOBS, 1):
        r = run_job(goal, i, len(JOBS))
        r["job_num"] = i
        r["difficulty"] = difficulty
        results.append(r)
        if r["status"] == "TIMEOUT":
            print("  ⚠ Timed out, continuing...")

    passed = sum(1 for r in results if r["status"] == "PASS")
    total_cost = sum(r["cost_usd"] for r in results)

    first3 = [r for r in results[:3] if r["cost_usd"] > 0]
    last3 = [r for r in results[-3:] if r["cost_usd"] > 0]
    first3_avg = sum(r["cost_usd"] for r in first3) / len(first3) if first3 else 0
    last3_avg = sum(r["cost_usd"] for r in last3) / len(last3) if last3 else 0
    cost_change = ((last3_avg - first3_avg) / first3_avg * 100) if first3_avg > 0 else 0

    print(f"\n{'=' * 66}")
    print("  SIMULATION COMPLETE")
    print(f"{'=' * 66}")

    print("\n  Job │ Difficulty │  Cost    │ Status │ Time")
    print(f"  {'─' * 4}┼{'─' * 11}┼{'─' * 9}┼{'─' * 7}┼{'─' * 6}")
    for r in results:
        icon = "✓" if r["status"] == "PASS" else ("⏱" if r["status"] == "TIMEOUT" else "✗")
        print(f"  {r['job_num']:3d} │ {r['difficulty']:9s} │ ${r['cost_usd']:.5f} │ {icon} {r['status']:7s} │ {r['wall_sec']:.0f}s")

    print(f"\n  Passed:         {passed}/{len(JOBS)}")
    print(f"  Total cost:     ${total_cost:.4f}")
    print(f"  Avg cost 1-3:   ${first3_avg:.4f}")
    print(f"  Avg cost 8-10:  ${last3_avg:.4f}")
    print(f"  Cost trend:     {cost_change:+.1f}%")

    if cost_change < -5:
        print(f"\n  ✓ COMPOUNDING WORKS: last jobs ~{abs(int(cost_change))}% cheaper than first")
    elif cost_change > 5:
        print("\n  ⚠ Cost increased — harder late jobs mask compounding")
    else:
        print("\n  → Flat trend — broader sample needed")

    csv_path = ROOT / ".awos" / "dummy_customer_results.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["job_num", "difficulty", "status",
                                           "tasks_completed", "total_tasks",
                                           "cost_usd", "wall_sec", "session_id"])
        w.writeheader()
        w.writerows(results)
    print(f"\nResults saved: {csv_path}")


if __name__ == "__main__":
    main()
