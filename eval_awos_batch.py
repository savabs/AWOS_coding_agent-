#!/usr/bin/env python3
"""
AWOS Batch Evaluation — Non-interactive version.
Runs all 4 levels, saves report to .awos/eval_report.txt
"""
import sys, os, subprocess, time, json, textwrap, traceback
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, "scaffold")
load_dotenv(Path(__file__).parent / ".env")
os.environ.setdefault("AWOS_MONTHLY_BUDGET", "20.0")

REPORT_PATH = ".awos/eval_report.txt"
Path(".awos").mkdir(exist_ok=True)


def report_line(text):
    with open(REPORT_PATH, "a", encoding="utf-8") as fh:
        fh.write(text + "\n")
    print(text)


def clear_report():
    with open(REPORT_PATH, "w", encoding="utf-8"):
        pass


def header(text, char="═"):
    line = char * 62
    report_line(f"\n{line}")
    report_line(f"  {text}")
    report_line(f"{line}")


def check_python_syntax(filepath):
    result = subprocess.run(
        ["python3", "-c", f"import ast; ast.parse(open('{filepath}').read())"],
        capture_output=True, text=True
    )
    return result.returncode == 0, result.stderr.strip()


def run_pytest(test_filter):
    result = subprocess.run(
        ["python3", "-m", "pytest", test_filter, "-q", "--tb=no", "--no-header"],
        capture_output=True, text=True,
        cwd="/home/becmachlean/2024/projects/AWOS_coding_agent"
    )
    output = result.stdout + result.stderr
    passed = failed = 0
    for line in output.splitlines():
        if "passed" in line:
            import re
            m = re.search(r"(\d+) passed", line)
            if m: passed = int(m.group(1))
            m = re.search(r"(\d+) failed", line)
            if m: failed = int(m.group(1))
    return passed, failed


def git_diff(filepath):
    result = subprocess.run(
        ["git", "diff", "--", filepath],
        capture_output=True, text=True,
        cwd="/home/becmachlean/2024/projects/AWOS_coding_agent"
    )
    return result.stdout


def git_restore(filepath):
    subprocess.run(
        ["git", "checkout", "HEAD", "--", filepath],
        capture_output=True,
        cwd="/home/becmachlean/2024/projects/AWOS_coding_agent"
    )


def read_budget():
    from agent.budget_ledger import get_ledger
    return get_ledger().get_status()["spent"]


def print_diff(diff_text, max_lines=40):
    lines = diff_text.strip().splitlines()
    if not lines:
        report_line("    (no diff — file may not have changed)")
        return
    for line in lines[:max_lines]:
        report_line(f"  {line}")
    if len(lines) > max_lines:
        report_line(f"  ... [{len(lines) - max_lines} more lines]")


# ── Evaluation Harness ────────────────────────────────────────────────────────

class EvalResult:
    def __init__(self, name, level, target_file):
        self.name = name
        self.level = level
        self.target_file = target_file
        self.success = False
        self.syntax_ok = False
        self.tests_pass = False
        self.tasks_completed = 0
        self.tasks_total = 0
        self.time_s = 0.0
        self.cost_delta = 0.0
        self.diff = ""
        self.error_trace = ""
        self.notes = []

    def score(self):
        pts = 0
        if self.success: pts += 40
        if self.syntax_ok: pts += 30
        if self.tests_pass: pts += 20
        if len(self.diff) > 50: pts += 10
        return pts


def run_task(name, level, goal, target_files, codebase_root="."):
    from agent.orchestrator import Orchestrator

    header(f"L{level}: {name}", char="─")
    report_line(f"  Goal   : {goal}")
    report_line(f"  Files  : {', '.join(target_files)}")

    res = EvalResult(name, level, target_files[0])
    budget_before = read_budget()

    try:
        t0 = time.time()
        orch = Orchestrator()
        result = orch.execute_feature(goal=goal, codebase_root=codebase_root)
        res.time_s = time.time() - t0
        res.success = result.get("success", False)
        res.tasks_completed = result.get("tasks_completed", 0)
        res.tasks_total = result.get("total_tasks", 0)
        res.cost_delta = read_budget() - budget_before
    except Exception as e:
        res.error_trace = traceback.format_exc()
        res.notes.append(f"Exception: {e}")
        res.time_s = time.time() - t0

    # Syntax checks
    for tf in target_files:
        fp = Path(codebase_root) / tf
        if fp.exists():
            ok, err = check_python_syntax(str(fp.resolve()))
            res.syntax_ok = res.syntax_ok or ok
            if not ok:
                res.notes.append(f"Syntax error in {tf}: {err[:120]}")

    res.diff = "\n".join(
        git_diff(tf) for tf in target_files
    )
    return res


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    clear_report()
    header("AWOS CAPABILITY EVALUATION (BATCH)", char="═")
    budget_start = read_budget()
    results = []

    # L1: Easy
    r1 = run_task(
        "Add __repr__ to ErrorPattern", 1,
        "Add a __repr__ method to the ErrorPattern dataclass in scaffold/agent/error_pattern_store.py that returns: 'ErrorPattern(file=X, type=Y, ts=Z)'",
        ["scaffold/agent/error_pattern_store.py"],
    )
    p, f = run_pytest("tests/test_reflexion_memory.py")
    r1.tests_pass = (f == 0 and p > 0)
    report_line(f"\n  Syntax OK: {'✓' if r1.syntax_ok else '✗'}")
    report_line(f"  Tests:     {p} passed, {f} failed")
    report_line(f"  Cost:      ${r1.cost_delta:.4f}")
    report_line(f"  Time:      {r1.time_s:.1f}s")
    report_line(f"  Diff:")
    print_diff(r1.diff)
    if r1.error_trace:
        report_line(f"  Error trace:\n{r1.error_trace[:500]}")
    results.append(r1)

    # L2: Medium
    r2 = run_task(
        "Add exponential backoff to _cheap_call()", 2,
        "In scaffold/agent/orchestrator.py, modify _cheap_call() to retry up to 3 times with exponential backoff (delay: 0.5s, 1s, 2s). Only retry on network/API errors (Exception). If all retries fail, return empty string. Keep existing logging.",
        ["scaffold/agent/orchestrator.py"],
    )
    ok, err = check_python_syntax("scaffold/agent/orchestrator.py")
    r2.syntax_ok = ok
    p, f = run_pytest("tests/test_reflexion_memory.py")
    r2.tests_pass = (f == 0 and p > 0)
    report_line(f"\n  Syntax OK: {'✓' if r2.syntax_ok else '✗'}")
    report_line(f"  Tests:     {p} passed, {f} failed")
    report_line(f"  Cost:      ${r2.cost_delta:.4f}")
    report_line(f"  Time:      {r2.time_s:.1f}s")
    report_line(f"  Diff:")
    print_diff(r2.diff)
    if r2.error_trace:
        report_line(f"  Error trace:\n{r2.error_trace[:500]}")
    results.append(r2)
    git_restore("scaffold/agent/orchestrator.py")

    # L3: Hard
    r3 = run_task(
        "Add snapshot() to ToolPerformanceTracker", 3,
        "Add a snapshot() method to ToolPerformanceTracker in scaffold/agent/core/performance_tracker.py. snapshot() returns a dict: {total_tasks, success_rate (float 0-1), avg_latency_ms, best_model (str or None)}. Then in scaffold/agent/orchestrator.py, at the end of the EXECUTION SUMMARY print block, add a one-line print of the snapshot: '[TRACKER] total=X success=Y% avg_ms=Z best=M'",
        ["scaffold/agent/core/performance_tracker.py", "scaffold/agent/orchestrator.py"],
    )
    ok1, e1 = check_python_syntax("scaffold/agent/core/performance_tracker.py")
    ok2, e2 = check_python_syntax("scaffold/agent/orchestrator.py")
    r3.syntax_ok = ok1 and ok2
    p, f = run_pytest("scaffold/tests/test_tool_reflection.py")
    r3.tests_pass = (f == 0 and p > 0)
    report_line(f"\n  Syntax OK: {'✓' if r3.syntax_ok else '✗'}")
    report_line(f"  Tests:     {p} passed, {f} failed")
    report_line(f"  Cost:      ${r3.cost_delta:.4f}")
    report_line(f"  Time:      {r3.time_s:.1f}s")
    report_line(f"  Diff — performance_tracker.py:")
    print_diff(git_diff("scaffold/agent/core/performance_tracker.py"))
    report_line(f"  Diff — orchestrator.py:")
    print_diff(git_diff("scaffold/agent/orchestrator.py"))
    if r3.error_trace:
        report_line(f"  Error trace:\n{r3.error_trace[:500]}")
    results.append(r3)
    git_restore("scaffold/agent/core/performance_tracker.py")
    git_restore("scaffold/agent/orchestrator.py")

    # L4: Extreme
    r4 = run_task(
        "Refactor Worker: extract _build_prompt()", 4,
        "Refactor scaffold/agent/worker.py: extract the prompt-building logic from execute_task() into a new private method _build_prompt(self, task, file_content, codebase_context, attempt, error_context, past_critiques, vector_chunks, examples_section, skill_section, symbol_section) -> str. _build_prompt() returns the full system prompt string. execute_task() calls _build_prompt() and passes the result directly to the LLM. All existing behaviour must be preserved.",
        ["scaffold/agent/worker.py"],
    )
    ok, err = check_python_syntax("scaffold/agent/worker.py")
    r4.syntax_ok = ok
    p, f = run_pytest("scaffold/tests/test_planner_worker.py")
    r4.tests_pass = (f == 0 and p > 0)
    report_line(f"\n  Syntax OK: {'✓' if r4.syntax_ok else '✗'}")
    report_line(f"  Tests:     {p} passed, {f} failed")
    report_line(f"  Cost:      ${r4.cost_delta:.4f}")
    report_line(f"  Time:      {r4.time_s:.1f}s")
    report_line(f"  Diff:")
    print_diff(r4.diff)
    if r4.error_trace:
        report_line(f"  Error trace:\n{r4.error_trace[:500]}")
    results.append(r4)
    git_restore("scaffold/agent/worker.py")

    # ── Final Scorecard ──────────────────────────────────────────────────
    total_cost = read_budget() - budget_start
    avg_score = sum(r.score() for r in results) / len(results)

    header("EVALUATION SCORECARD", char="═")
    report_line(f"\n  Level  Task                                     Score  Time    Cost     Verdict")
    report_line(f"  {'─'*6} {'─'*42} {'─'*6} {'─'*7} {'─'*8} {'─'*12}")
    for r in results:
        verdict = "EXCELLENT" if r.score() >= 90 else "GOOD" if r.score() >= 70 else "PARTIAL" if r.score() >= 50 else "FAILED"
        report_line(f"  L{r.level:<5} {r.name:<42} {r.score():<6} {r.time_s:>6.1f}s ${r.cost_delta:>7.4f} {verdict}")

    report_line(f"\n  {'─' * 80}")
    report_line(f"  Overall Score:   {avg_score:.0f} / 100")
    report_line(f"  Total Cost:      ${total_cost:.4f}")
    report_line(f"  Total Time:      {sum(r.time_s for r in results):.1f}s")

    if avg_score >= 80:
        report_line(f"  VERDICT: Production-grade agent.")
    elif avg_score >= 60:
        report_line(f"  VERDICT: Strong agent. Complex refactors need work.")
    elif avg_score >= 40:
        report_line(f"  VERDICT: Emerging agent. Core pipeline works.")
    else:
        report_line(f"  VERDICT: Early-stage. Needs improvement.")

    report_line(f"\n  Notes per task:")
    for r in results:
        if r.notes:
            report_line(f"    L{r.level}:")
            for n in r.notes:
                report_line(f"      ⚠ {n}")

    report_line(f"\n  All code changes reverted. Report saved to: {REPORT_PATH}")


if __name__ == "__main__":
    main()
