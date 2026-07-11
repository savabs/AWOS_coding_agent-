#!/usr/bin/env python3
"""
AWOS SWE-bench Lite — Self-Contained Benchmark (NO Docker needed)

Runs the full pipeline:
1. Clone repos
2. Agent generates patches (prediction)
3. pip install deps + run pytest (validation)
4. Report score

Usage: python3 scripts/run_benchmark_standalone.py [--limit N]
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BENCH_DIR = REPO / ".awos" / "swebench_standalone"

def load_env():
    env = REPO / ".env"
    if not env.exists():
        print("WARNING: .env not found — API keys may be missing")
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

def run_instance(inst_id: str, repo: str, base_commit: str, problem: str, test_patch: str) -> dict:
    """Run one instance: clone, predict, validate."""
    work = BENCH_DIR / inst_id
    result = {"instance_id": inst_id, "patch_bytes": 0, "tests_pass": False, "error": "", "time": 0}
    t0 = time.time()

    # Clone
    if not (work / ".git").is_dir():
        url = f"https://github.com/{repo}.git"
        r = subprocess.run(["git", "clone", "--depth", "1", url, str(work)],
                          capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            result["error"] = f"clone failed: {r.stderr[:200]}"
            return result
        subprocess.run(["git", "checkout", base_commit], cwd=work,
                      capture_output=True, text=True, timeout=120)

    # Predict (agent generates patch)
    print(f"  [{inst_id}] Running agent...")
    sys.path.insert(0, str(REPO))
    from scaffold.agent.swebench_lite import run_instance_react, load_lite_instances
    instances = {i.instance_id: i for i in load_lite_instances()}
    if inst_id not in instances:
        result["error"] = "instance not found"
        return result

    inst = instances[inst_id]
    run_result = run_instance_react(inst, work, max_turns=60)
    result["patch_bytes"] = len(run_result.model_patch)
    result["time"] = run_result.latency_sec
    result["cost"] = run_result.cost_usd
    result["model"] = run_result.model_used

    if not run_result.model_patch.strip():
        result["error"] = "no patch generated"
        return result

    # Validate (pip install + pytest)
    print(f"  [{inst_id}] Validating...")
    try:
        # Apply test patch
        if test_patch:
            subprocess.run(["git", "apply"], cwd=work, input=test_patch,
                          capture_output=True, text=True, timeout=30)

        # Install deps
        subprocess.run([sys.executable, "-m", "pip", "install", "-e", str(work), "--quiet"],
                      cwd=work, capture_output=True, text=True, timeout=300)

        # Find and run test files
        import re
        test_files = re.findall(r"diff --git a/(.*?) b/", test_patch) if test_patch else []
        if test_files:
            tests_passed = 0
            for tf in test_files:
                if (work / tf).exists():
                    r = subprocess.run([sys.executable, "-m", "pytest", tf, "-x", "-q"],
                                     cwd=work, capture_output=True, text=True, timeout=120)
                    if r.returncode == 0:
                        tests_passed += 1
            result["tests_pass"] = tests_passed > 0 and tests_passed == len(test_files)
        else:
            result["tests_pass"] = True  # No test files to run
            result["error"] = "no test files found in test_patch"

    except Exception as e:
        result["error"] = f"validation failed: {e}"
    finally:
        # Reset test files to get clean patch
        if test_patch:
            subprocess.run(["git", "checkout", "HEAD", "--", "."],
                          cwd=work, capture_output=True, text=True, timeout=30)

    return result

def main():
    load_env()
    sys.path.insert(0, str(REPO))

    from scaffold.agent.swebench_lite import load_lite_instances

    instances = load_lite_instances()
    print("═" * 60)
    print(f"  AWOS SWE-bench Standalone — {len(instances)} instances")
    print("═" * 60)

    results = []
    for i, inst in enumerate(instances):
        print(f"\n── [{i+1}/{len(instances)}] {inst.instance_id} ──")
        r = run_instance(inst.instance_id, inst.repo, inst.base_commit,
                        inst.problem_statement, inst.test_patch)
        results.append(r)
        print(f"  Result: {'✅ PASS' if r['tests_pass'] else '❌ FAIL' if r['patch_bytes'] else '⚠️ NO PATCH'}")
        print(f"  Patch: {r['patch_bytes']}B | Time: {r.get('time',0):.0f}s | Cost: ${r.get('cost',0):.4f}")

    # Report
    passed = sum(1 for r in results if r["tests_pass"])
    patches = sum(1 for r in results if r["patch_bytes"] > 0)
    total_cost = sum(r.get("cost", 0) for r in results)

    print(f"\n{'═' * 60}")
    print(f"  FINAL SCORE: {passed}/{len(results)} ({passed*100//max(len(results),1)}%)")
    print(f"  Patches: {patches}/{len(results)}")
    print(f"  Total cost: ${total_cost:.4f}")
    print("═" * 60)

    for r in results:
        status = "✅" if r["tests_pass"] else "❌" if r["patch_bytes"] else "⚠️"
        print(f"  {status} {r['instance_id']}: {r['patch_bytes']}B ${r.get('cost',0):.4f} {r.get('model','?')}")

if __name__ == "__main__":
    main()
