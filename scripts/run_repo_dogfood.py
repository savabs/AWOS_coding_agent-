#!/usr/bin/env python3
"""
Repo dogfood — run ReAct on the real AWOS codebase (tough integration task).

Usage:
  python3 scripts/run_repo_dogfood.py
  python3 scripts/run_repo_dogfood.py --goal "your custom goal"

Verification is independent pytest + CLI smoke after the agent finishes.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

DEFAULT_GOAL = """\
Implement `awos stats --costs` end-to-end on THIS repo (real production CLI).

ANCHORS (grep these first — do NOT read awos.py line-by-line):
- `def cmd_stats` ~line 776 — mirror the existing `--savings` early-return block pattern
- `stats.add_argument("--savings"` ~line 1255 — add `--costs` on the next line

Requirements:
1. Add `--costs` to stats subparser (alongside --json and --savings).
2. In cmd_stats: NEW block BEFORE the savings block: if --costs, call get_ledger().get_status(),
   print month/spent/budget/remaining/percent_used/requests/tokens/cache_savings/avg_cost_per_request,
   then `return`. Use AWOS_MONTHLY_BUDGET env (default 20.0). Do NOT duplicate the block.
3. Create tests/test_awos_stats_costs.py — mock ledger, >=2 pytest cases for --costs output.
4. Do NOT modify or remove the existing --savings logic.
5. Run pytest tests/test_awos_stats_costs.py -q, then finish.

Touch only: awos.py, tests/test_awos_stats_costs.py (new).
"""

VERIFY_TESTS = ["tests/test_awos_stats_costs.py"]
CLI_COSTS = [sys.executable, "awos.py", "stats", "--costs"]
CLI_SAVINGS = [sys.executable, "awos.py", "stats", "--savings"]


def _load_dotenv() -> None:
    env = REPO / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _git_branch() -> str:
    r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    return (r.stdout or "").strip() or "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description="AWOS repo dogfood (ReAct on real codebase)")
    parser.add_argument("--goal", default=DEFAULT_GOAL, help="Task goal for the agent")
    parser.add_argument("--max-turns", type=int, default=40)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="git checkout awos.py before run (clean retry)",
    )
    args = parser.parse_args()

    _load_dotenv()
    os.chdir(REPO)

    if args.reset:
        subprocess.run(["git", "checkout", "HEAD", "--", "awos.py"], cwd=REPO, check=False)
        print("[DOGFOOD] Reset awos.py to HEAD\n")

    branch = _git_branch()
    print("═" * 70)
    print("AWOS REPO DOGFOOD — real codebase, integration task")
    print("═" * 70)
    print(f"Repo:   {REPO}")
    print(f"Branch: {branch}")
    print(f"Task:   awos stats --costs + tests (prior dogfood failures)")
    print("═" * 70)
    print()

    os.environ.setdefault("AWOS_REACT_WORKER", "1")
    os.environ.setdefault("AWOS_SAFE_TO_RUN_TESTS", "1")
    os.environ.setdefault("AWOS_ENABLE_WEB_TOOLS", "1")
    os.environ.setdefault("AWOS_REACT_MAX_TURNS", str(args.max_turns))
    os.environ.setdefault("ALLOW_CODE_EXEC", "1")

    sys.path.insert(0, str(REPO))
    from scaffold.agent.react_worker import ReActWorker

    task = {
        "task_id": "repo_dogfood_1",
        "file": "awos.py",
        "action": args.goal,
        "complexity": "high",
    }

    awos_path = REPO / "awos.py"
    snippet = awos_path.read_text(encoding="utf-8")[:12000]

    from scaffold.agent.repo_map import build_repo_map

    preview = build_repo_map(args.goal, REPO, primary_file="awos.py")
    print("[DOGFOOD] Repo map preview (first 1200 chars):")
    print(preview.get("summary", "")[:1200])
    print()

    worker = ReActWorker()
    print("[DOGFOOD] Starting ReAct on repo…\n")
    result = worker.execute_task(
        task=task,
        file_content=snippet,
        codebase_context={
            "architecture": "AWOS CLI + scaffold/agent kernel",
            "files": ["awos.py", "scaffold/agent/budget_ledger.py"],
        },
        project_root=str(REPO),
    )

    print()
    print(f"[DOGFOOD] ReAct success={result.get('success')}")
    print(f"[DOGFOOD] Summary: {(result.get('summary') or '')[:400]}")
    if not result.get("success"):
        print(f"[DOGFOOD] Error: {result.get('error')}")
    print(f"[DOGFOOD] Files changed: {result.get('files_changed')}")
    tr = result.get("test_result")
    if tr is not None:
        print(
            f"[DOGFOOD] Agent tests: passed={tr.passed} failed={tr.failed} "
            f"no_tests={tr.no_tests_found}"
        )

    print()
    print("─" * 70)
    print("INDEPENDENT VERIFICATION (not agent self-report)")
    print("─" * 70)

    ok = True
    for rel in VERIFY_TESTS:
        path = REPO / rel
        if not path.exists():
            print(f"✗ Missing expected file: {rel}")
            ok = False
            continue
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", rel, "-q", "--tb=short"],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        print(proc.stdout)
        if proc.stderr:
            print(proc.stderr, file=sys.stderr)
        if proc.returncode != 0:
            print(f"✗ pytest failed: {rel}")
            ok = False
        else:
            print(f"✓ pytest passed: {rel}")

    awos_src = (REPO / "awos.py").read_text(encoding="utf-8")
    costs_blocks = awos_src.count('getattr(args, "costs"')
    if costs_blocks != 1:
        print(f"✗ awos.py has {costs_blocks} --costs branches (want exactly 1)")
        ok = False
    else:
        print("✓ Single --costs branch in awos.py")

    compile_proc = subprocess.run(
        [sys.executable, "-m", "py_compile", "awos.py"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    if compile_proc.returncode != 0:
        print(f"✗ awos.py syntax error: {compile_proc.stderr[:300]}")
        ok = False
    else:
        print("✓ awos.py compiles")

    for label, cmd in (("--costs", CLI_COSTS), ("--savings", CLI_SAVINGS)):
        proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
        print()
        print(f"$ awos stats {label}")
        print(proc.stdout[:800] or "(no stdout)")
        if proc.stderr:
            print(proc.stderr[:300], file=sys.stderr)
        if proc.returncode != 0:
            print(f"✗ CLI `awos stats {label}` failed")
            ok = False
        elif label == "--costs" and "spent" not in proc.stdout.lower():
            print("✗ --costs output missing spent marker")
            ok = False
        elif label == "--savings" and "COMPOUNDING" not in proc.stdout:
            print("✗ --savings regression (missing savings report header)")
            ok = False
        else:
            print(f"✓ CLI `awos stats {label}` works")

    print()
    if ok and result.get("success"):
        print("═" * 70)
        print("✓ REPO DOGFOOD PASSED — real integration on AWOS codebase")
        print("═" * 70)
        print(f"Review: git diff ({branch})")
        return 0

    print("═" * 70)
    print("✗ REPO DOGFOOD FAILED — see diff and logs above")
    print("═" * 70)
    subprocess.run(["git", "diff", "--stat"], cwd=REPO)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
