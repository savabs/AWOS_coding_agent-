#!/usr/bin/env python3
"""
prove_agent_loop_executor.py — live proof for docs/specs/agent_loop_executor_spec.md.

Runs the real Orchestrator, with AWOS_EXECUTOR=agent_loop, on staged benchmark
cases — each in a fresh git repo with a fresh Orchestrator — then checks the
outcome independently with the case's own tests.

    python3 scripts/prove_agent_loop_executor.py                        # b1_constant_mismatch
    python3 scripts/prove_agent_loop_executor.py --case c1_reuse_existing_helper
    python3 scripts/prove_agent_loop_executor.py --all                  # all 12 cases

About $0.001 per case on DeepSeek v4 Flash via OpenRouter. Exits non-zero
unless every case passes — orchestrator reports success AND the case's tests
pass on the file it left — and every request went to openrouter.ai.
"""
import argparse
import collections
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scaffold"))
sys.path.insert(0, str(REPO / "scaffold" / "agent"))

from dotenv import load_dotenv

load_dotenv(REPO / ".env")
os.environ.update({
    "AWOS_EXECUTOR": "agent_loop",
    "AWOS_SAFE_TO_RUN_TESTS": "1",      # let the orchestrator verify with tests
    "AWOS_USE_WORKTREE": "false",       # the staged repo is already throwaway
    "AWOS_ENABLE_CLARIFICATION": "false",
    "AWOS_ENABLE_PLAN_REVIEW": "false",
})

import httpx

hosts = collections.Counter()


def _hook(module):
    orig = module.HTTPTransport.handle_request

    def handle(self, request):
        hosts[request.url.host] += 1
        return orig(self, request)

    module.HTTPTransport.handle_request = handle


_hook(httpx)
try:
    import httpx2  # openai>=3 vendors its own httpx

    _hook(httpx2)
except ImportError:
    pass

from agent.bench_cases import discover_cases, load_case, run_case_tests, stage_case


def _ledger_entries() -> list:
    try:
        return json.loads((Path.cwd() / ".awos" / "budget.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def run_case(case) -> dict:
    """One case: fresh git repo, fresh Orchestrator, independent verdict."""
    hosts.clear()
    ledger_before = len(_ledger_entries())
    started = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        project = stage_case(case, tmp)
        git = ["git", "-C", str(project)]
        subprocess.run(git + ["init", "-q"], check=True)
        subprocess.run(git + ["add", "-A"], check=True)
        subprocess.run(git + ["-c", "user.email=proof@awos", "-c", "user.name=proof",
                              "commit", "-q", "-m", "staged case"], check=True)

        from scaffold.agent.orchestrator import Orchestrator

        task = {"task_id": 1, "file": case.target_file, "action": case.action, "complexity": "low"}
        print(f"\n=== Orchestrator, AWOS_EXECUTOR=agent_loop, case {case.name} ===\n", flush=True)
        try:
            report = Orchestrator().execute_feature(
                goal=case.action,
                codebase_root=str(project),
                pre_planned_tasks=[task],
                auto_approve_plan=True,
            )
        except Exception as exc:  # a crash is a result, not the end of the run
            report = {"success": False, "error": f"{type(exc).__name__}: {exc}"}

        edited = (project / case.target_file).read_text(encoding="utf-8")
        tests_pass, _ = run_case_tests(case, source=edited)
        diff = subprocess.run(git + ["diff"], capture_output=True, text=True).stdout

    return {
        "case": case.name,
        "tier": case.tier,
        "multi_file": case.is_multi_file,
        "orchestrator_success": bool(report.get("success")),
        "tasks_completed": report.get("tasks_completed"),
        "tasks_failed": report.get("tasks_failed"),
        # From the BudgetLedger — the record the budget hard-stop enforces.
        "cost_usd": round(sum(float(e.get("cost", 0)) for e in _ledger_entries()[ledger_before:]), 6),
        "tests_pass": tests_pass,
        "error": report.get("error", ""),
        "hosts": dict(hosts),
        "diff": diff,
        "seconds": round(time.monotonic() - started, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", default="b1_constant_mismatch")
    parser.add_argument("--all", action="store_true", help="Run every case in tests/bug_cases")
    args = parser.parse_args()

    cases_dir = REPO / "tests" / "bug_cases"
    names = [d.name for d in discover_cases(str(cases_dir))] if args.all else [args.case]

    results = []
    for index, name in enumerate(names, 1):
        print(f"\n########## [{index}/{len(names)}] {name} ##########", flush=True)
        r = run_case(load_case(cases_dir / name))
        results.append(r)
        ok = r["orchestrator_success"] and r["tests_pass"]
        print(f"\n[{index}/{len(names)}] {name}: "
              f"orchestrator={'success' if r['orchestrator_success'] else 'FAIL'} "
              f"tests={'pass' if r['tests_pass'] else 'FAIL'} -> {'PASS' if ok else 'FAIL'} "
              f"({r['seconds']}s) {r['error']}", flush=True)
        if not args.all:
            print("diff left behind:\n" + (r["diff"] or "  (none)"))

    all_hosts = collections.Counter()
    for r in results:
        all_hosts.update(r["hosts"])
    # A case passes only if the orchestrator says so AND the case's own tests agree.
    passed = [r for r in results if r["orchestrator_success"] and r["tests_pass"]]
    disagree = [r["case"] for r in results if r["orchestrator_success"] != r["tests_pass"]]

    print("\n" + "=" * 78)
    print(f"  {'case':<30} {'tier':<5} {'orchestrator':<13} {'tests':<6} {'verdict':<8} secs")
    print("  " + "-" * 74)
    for r in results:
        ok = r["orchestrator_success"] and r["tests_pass"]
        mark = "*" if r["multi_file"] else " "
        print(f"  {r['case']:<30}{mark}{r['tier']:<5}"
              f"{'success' if r['orchestrator_success'] else 'fail':<13} "
              f"{'pass' if r['tests_pass'] else 'FAIL':<6} {'PASS' if ok else 'FAIL':<8} {r['seconds']}")
    print("  " + "-" * 74)
    print(f"  passed {len(passed)}/{len(results)}   "
          f"cost ${sum(r['cost_usd'] for r in results):.4f} (BudgetLedger)   hosts {dict(all_hosts)}")
    if disagree:
        print(f"  orchestrator and tests DISAGREE on: {', '.join(disagree)}")
    print("=" * 78)

    out = REPO / ".awos" / f"orchestrator_proof_{time.strftime('%Y%m%dT%H%M%S')}.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"  Saved {out.relative_to(REPO)}")
    ok = len(passed) == len(results) and set(all_hosts) <= {"openrouter.ai"}
    print("PROOF:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
