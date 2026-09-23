#!/usr/bin/env python3
"""
prove_agent_loop_executor.py — live proof for docs/specs/agent_loop_executor_spec.md.

Runs the real Orchestrator, with AWOS_EXECUTOR=agent_loop, on a staged
benchmark case in a fresh git repo, then checks the outcome independently with
the case's own tests.

    python3 scripts/prove_agent_loop_executor.py                        # b1_constant_mismatch
    python3 scripts/prove_agent_loop_executor.py --case c1_reuse_existing_helper

Costs about $0.001 on DeepSeek v4 Flash via OpenRouter. Exits non-zero unless
the orchestrator reports success AND the case's tests pass on the file it left.
"""
import argparse
import collections
import os
import subprocess
import sys
import tempfile
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

from agent.bench_cases import load_case, run_case_tests, stage_case


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", default="b1_constant_mismatch")
    args = parser.parse_args()

    case = load_case(REPO / "tests" / "bug_cases" / args.case)
    with tempfile.TemporaryDirectory() as tmp:
        project = stage_case(case, tmp)
        git = ["git", "-C", str(project)]
        subprocess.run(git + ["init", "-q"], check=True)
        subprocess.run(git + ["add", "-A"], check=True)
        subprocess.run(git + ["-c", "user.email=proof@awos", "-c", "user.name=proof",
                              "commit", "-q", "-m", "staged case"], check=True)

        from scaffold.agent.orchestrator import Orchestrator

        task = {"task_id": 1, "file": case.target_file, "action": case.action, "complexity": "low"}
        print(f"\n=== Orchestrator, AWOS_EXECUTOR=agent_loop, case {case.name} ===\n")
        report = Orchestrator().execute_feature(
            goal=case.action,
            codebase_root=str(project),
            pre_planned_tasks=[task],
            auto_approve_plan=True,
        )

        edited = (project / case.target_file).read_text(encoding="utf-8")
        tests_pass, _ = run_case_tests(case, source=edited)
        diff = subprocess.run(git + ["diff"], capture_output=True, text=True).stdout

    print("\n=== Result ===")
    print(f"orchestrator success : {report.get('success')}")
    print(f"tasks completed      : {report.get('tasks_completed')}  failed: {report.get('tasks_failed')}")
    print(f"executor in log      : {[e.get('reason', '')[:60] for e in report.get('execution_log', [])]}")
    print(f"case tests (independent) pass: {tests_pass}")
    print(f"hosts contacted      : {dict(hosts)}")
    print("diff left behind:\n" + (diff or "  (none)"))
    ok = bool(report.get("success")) and tests_pass
    print("PROOF:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
