#!/usr/bin/env python3
"""
long_tasks.py — realistic, user-shaped tasks for AWOS (milestone M0.5).

Each task lives in tests/long_tasks/<name>/:
    task.json      {"name", "kind", "goal", "max_turns", "max_cost_usd", "timeout_min"}
    project/       the repo the agent works in, in its starting state
    hidden_tests/  acceptance tests the agent never sees
    reference/     a full-file overlay onto project/ that solves the task

    python3 scripts/long_tasks.py validate            # every task is well-formed
    python3 scripts/long_tasks.py run                 # run all through the orchestrator
    python3 scripts/long_tasks.py run --task NAME

`run` hands the orchestrator only the user's goal (planner path), in a fresh
process per task with the task's timeout, then judges the result with the
hidden tests. Tasks run one after another: they share the BudgetLedger file.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TASKS_DIR = REPO / "tests" / "long_tasks"
PY = sys.executable


# ── Task discovery and staging ────────────────────────────────────────────────


def discover(names: list[str] | None = None) -> list[Path]:
    found = sorted(p.parent for p in TASKS_DIR.glob("*/task.json"))
    if names:
        found = [p for p in found if p.name in set(names)]
    return found


def load(task_dir: Path) -> dict:
    return json.loads((task_dir / "task.json").read_text(encoding="utf-8"))


def stage(task_dir: Path, dest: Path, with_reference: bool = False) -> Path:
    """Copy project/ (optionally with reference/ overlaid) to dest."""
    shutil.copytree(task_dir / "project", dest)
    if with_reference:
        ref = task_dir / "reference"
        for src in ref.rglob("*"):
            if src.is_file():
                target = dest / src.relative_to(ref)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
    return dest


def add_hidden_tests(task_dir: Path, project: Path) -> None:
    target = project / "hidden_tests"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(task_dir / "hidden_tests", target)


def pytest(project: Path, *paths: str, timeout: int = 600) -> dict:
    """Run pytest; return counts parsed from its summary line."""
    try:
        proc = subprocess.run(
            [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", *paths],
            cwd=project, capture_output=True, text=True, timeout=timeout,
        )
        out = proc.stdout + proc.stderr
        code = proc.returncode
    except subprocess.TimeoutExpired:
        return {"passed": 0, "failed": 0, "errors": 0, "ok": False, "summary": "pytest timed out"}
    counts = {k: 0 for k in ("passed", "failed", "errors")}
    for num, word in re.findall(r"(\d+) (passed|failed|errors?)", out):
        counts["errors" if word.startswith("error") else word] += int(num)
    summary = next((ln for ln in reversed(out.strip().splitlines()) if ln.strip()), "")
    return {**counts, "ok": code == 0, "summary": re.sub(r"\x1b\[[0-9;]*m", "", summary)[:160]}


# ── validate ──────────────────────────────────────────────────────────────────


def validate(names: list[str] | None) -> int:
    bad = 0
    for task_dir in discover(names):
        name = task_dir.name
        missing = [p for p in ("task.json", "project", "hidden_tests", "reference")
                   if not (task_dir / p).exists()]
        if missing:
            print(f"  {name:<28} INVALID  missing {missing}")
            bad += 1
            continue
        with tempfile.TemporaryDirectory() as tmp:
            start = stage(task_dir, Path(tmp) / "start")
            add_hidden_tests(task_dir, start)
            hidden_start = pytest(start, "hidden_tests")

            solved = stage(task_dir, Path(tmp) / "solved", with_reference=True)
            add_hidden_tests(task_dir, solved)
            hidden_solved = pytest(solved, "hidden_tests")
            has_visible = (solved / "tests").is_dir()
            visible_solved = pytest(solved, "tests") if has_visible else {"ok": True}
            visible_start = pytest(start, "tests") if has_visible else {"ok": True}

        problems = []
        if hidden_start["ok"]:
            problems.append("hidden tests PASS on the starting project (measures nothing)")
        if not hidden_solved["ok"]:
            problems.append(f"hidden tests fail with reference ({hidden_solved['summary']})")
        if not visible_start["ok"]:
            problems.append("project's own tests fail on the starting project")
        if not visible_solved["ok"]:
            problems.append("reference breaks the project's own tests")
        status = "ok" if not problems else "INVALID"
        bad += bool(problems)
        print(f"  {name:<28} {status:<8} start: {hidden_start['summary']} | "
              f"reference: {hidden_solved['summary']}")
        for p in problems:
            print(f"      - {p}")
    return 1 if bad else 0


# ── run ───────────────────────────────────────────────────────────────────────


def _ledger() -> list:
    try:
        return json.loads((REPO / ".awos" / "budget.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def run_one_in_process(task_dir: Path, project: Path) -> None:
    """Child process: hand the goal to a fresh Orchestrator (planner path)."""
    sys.path[:0] = [str(REPO), str(REPO / "scaffold"), str(REPO / "scaffold" / "agent")]
    from dotenv import load_dotenv

    load_dotenv(REPO / ".env")
    os.environ.update({
        "AWOS_EXECUTOR": "agent_loop",
        "AWOS_SAFE_TO_RUN_TESTS": "1",
        "AWOS_USE_WORKTREE": "false",
        "AWOS_ENABLE_CLARIFICATION": "false",
        "AWOS_ENABLE_PLAN_REVIEW": "false",
        "AWOS_MAX_RUN_COST": str(load(task_dir).get("max_cost_usd", 1.0)),
    })
    from scaffold.agent.orchestrator import Orchestrator

    report = Orchestrator().execute_feature(
        goal=load(task_dir)["goal"],
        codebase_root=str(project),
        pre_planned_tasks=None,
        auto_approve_plan=True,
    )
    (project.parent / "report.json").write_text(json.dumps({
        k: report.get(k) for k in ("success", "tasks_completed", "tasks_failed")
    }), encoding="utf-8")


def run(names: list[str] | None) -> int:
    results = []
    tasks = discover(names)
    for index, task_dir in enumerate(tasks, 1):
        spec = load(task_dir)
        print(f"\n########## [{index}/{len(tasks)}] {spec['name']} ({spec['kind']}) ##########", flush=True)
        print(f"GOAL: {spec['goal']}\n", flush=True)
        ledger_before = len(_ledger())
        started = time.monotonic()
        with tempfile.TemporaryDirectory() as tmp:
            project = stage(task_dir, Path(tmp) / "project")
            git = ["git", "-C", str(project)]
            subprocess.run(git + ["init", "-q"], check=True)
            subprocess.run(git + ["add", "-A"], check=True)
            subprocess.run(git + ["-c", "user.email=bench@awos", "-c", "user.name=bench",
                                  "commit", "-q", "-m", "start"], check=True)

            timed_out = False
            try:
                subprocess.run(
                    [PY, __file__, "_child", str(task_dir), str(project)],
                    timeout=int(spec.get("timeout_min", 30)) * 60,
                    env={**os.environ, "PYTHONUNBUFFERED": "1"},
                )
            except subprocess.TimeoutExpired:
                timed_out = True
                print(f"[long_tasks] {spec['name']}: hit the {spec.get('timeout_min', 30)}-minute limit", flush=True)

            report_path = project.parent / "report.json"
            report = json.loads(report_path.read_text()) if report_path.exists() else {}
            changed = subprocess.run(git + ["status", "--porcelain"], capture_output=True, text=True).stdout
            add_hidden_tests(task_dir, project)
            hidden = pytest(project, "hidden_tests")
            visible = pytest(project, "tests") if (project / "tests").is_dir() else {"ok": True, "summary": "-"}

        cost = sum(float(e.get("cost", 0)) for e in _ledger()[ledger_before:])
        result = {
            "name": spec["name"], "kind": spec["kind"],
            "solved": hidden["ok"] and visible["ok"],
            "hidden": hidden, "visible_tests_ok": visible["ok"],
            "orchestrator_success": report.get("success"),
            "tasks_completed": report.get("tasks_completed"),
            "tasks_failed": report.get("tasks_failed"),
            "files_changed": len([ln for ln in changed.splitlines() if ln.strip()]),
            "cost_usd": round(cost, 4),
            "minutes": round((time.monotonic() - started) / 60, 1),
            "timed_out": timed_out,
        }
        results.append(result)
        print(f"\n[{index}/{len(tasks)}] {spec['name']}: {'SOLVED' if result['solved'] else 'not solved'} "
              f"— hidden {hidden['passed']}/{hidden['passed'] + hidden['failed'] + hidden['errors']} "
              f"· own tests {'ok' if visible['ok'] else 'BROKEN'} · ${cost:.4f} · {result['minutes']} min",
              flush=True)

    print("\n" + "=" * 86)
    print(f"  {'task':<24} {'kind':<12} {'verdict':<11} {'hidden':<9} {'own tests':<10} {'cost':<9} min")
    print("  " + "-" * 82)
    for r in results:
        h = r["hidden"]
        total = h["passed"] + h["failed"] + h["errors"]
        print(f"  {r['name']:<24} {r['kind']:<12} {'SOLVED' if r['solved'] else 'not solved':<11} "
              f"{h['passed']}/{total:<7} {'ok' if r['visible_tests_ok'] else 'BROKEN':<10} "
              f"${r['cost_usd']:<8} {r['minutes']}")
    solved = sum(r["solved"] for r in results)
    print("  " + "-" * 82)
    print(f"  solved {solved}/{len(results)}   cost ${sum(r['cost_usd'] for r in results):.4f}   "
          f"time {sum(r['minutes'] for r in results):.1f} min")
    print("=" * 86)
    out = REPO / ".awos" / f"long_tasks_{time.strftime('%Y%m%dT%H%M%S')}.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"  Saved {out.relative_to(REPO)}")
    return 0


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "_child":
        run_one_in_process(Path(sys.argv[2]), Path(sys.argv[3]))
        return 0
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["validate", "run"])
    parser.add_argument("--task", action="append", help="Only these tasks (repeatable)")
    args = parser.parse_args()
    return validate(args.task) if args.command == "validate" else run(args.task)


if __name__ == "__main__":
    sys.exit(main())
