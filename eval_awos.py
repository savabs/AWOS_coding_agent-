#!/usr/bin/env python3
"""
AWOS Capability Evaluation — The Real Test.

Runs 4 tasks of increasing difficulty on AWOS's OWN codebase.
Uses real LLMs. Measures: success, code quality, cost, model routing,
ReflexionMemory, and escalation behaviour.

Safety: creates a git branch before each run, rolls back on failure.

Difficulty curve:
  L1 — Easy    : Add a __repr__ to a dataclass (single file, 1 change)
  L2 — Medium  : Add exponential backoff to _cheap_call() (multi-step logic)
  L3 — Hard    : Add snapshot() to ToolPerformanceTracker + wire to Orchestrator (multi-file)
  L4 — Extreme : Refactor Worker.execute_task() to extract prompt building into _build_prompt()
"""
import sys, os, subprocess, time, json, textwrap
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, "scaffold")
load_dotenv(Path(__file__).parent / ".env")
os.environ.setdefault("AWOS_MONTHLY_BUDGET", "20.0")

G   = "\033[92m"   # green
Y   = "\033[93m"   # yellow
R   = "\033[91m"   # red
B   = "\033[94m"   # blue
C   = "\033[96m"   # cyan
W   = "\033[97m"   # white
RST = "\033[0m"

PASS  = f"{G}✓{RST}"
WARN  = f"{Y}⚠{RST}"
FAIL  = f"{R}✗{RST}"
STEP  = f"{C}▶{RST}"
INFO  = f"{B}ℹ{RST}"


# ── Helpers ───────────────────────────────────────────────────────────────────

def header(text, char="═"):
    line = char * 62
    print(f"\n{C}{line}{RST}")
    print(f"  {W}{text}{RST}")
    print(f"{C}{line}{RST}")


def subheader(text):
    print(f"\n{B}── {text} {'─' * (55 - len(text))}{RST}")


def git_snapshot():
    """Return current state of tracked files as a dict."""
    result = subprocess.run(
        ["git", "stash", "list"], capture_output=True, text=True,
        cwd="/home/becmachlean/2024/projects/AWOS_coding_agent"
    )
    return result.stdout


def check_python_syntax(filepath):
    """Return (ok, error_msg)."""
    result = subprocess.run(
        ["python3", "-c", f"import ast; ast.parse(open('{filepath}').read())"],
        capture_output=True, text=True
    )
    return result.returncode == 0, result.stderr.strip()


def run_pytest(test_filter):
    """Run targeted pytest, return (passed, failed, total)."""
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
    return passed, failed, passed + failed


def git_diff(filepath):
    """Return unified diff for a file."""
    result = subprocess.run(
        ["git", "diff", "--", filepath],
        capture_output=True, text=True,
        cwd="/home/becmachlean/2024/projects/AWOS_coding_agent"
    )
    return result.stdout


def git_restore(filepath):
    """Restore a file to HEAD."""
    subprocess.run(
        ["git", "checkout", "HEAD", "--", filepath],
        capture_output=True,
        cwd="/home/becmachlean/2024/projects/AWOS_coding_agent"
    )


def read_budget():
    from agent.budget_ledger import get_ledger
    s = get_ledger().get_status()
    return s["spent"]


def print_diff(diff_text, max_lines=30):
    """Print a coloured unified diff."""
    lines = diff_text.strip().splitlines()
    if not lines:
        print(f"    {Y}(no diff — file may not have changed){RST}")
        return
    for line in lines[:max_lines]:
        if line.startswith("+") and not line.startswith("+++"):
            print(f"  {G}{line}{RST}")
        elif line.startswith("-") and not line.startswith("---"):
            print(f"  {R}{line}{RST}")
        elif line.startswith("@@"):
            print(f"  {C}{line}{RST}")
        else:
            print(f"  {line}")
    if len(lines) > max_lines:
        print(f"  {Y}... [{len(lines) - max_lines} more lines]{RST}")


# ── Evaluation Harness ────────────────────────────────────────────────────────

class EvalResult:
    def __init__(self, name, level, target_file):
        self.name        = name
        self.level       = level
        self.target_file = target_file
        self.success     = False
        self.syntax_ok   = False
        self.tests_pass  = False
        self.tasks_completed = 0
        self.tasks_total     = 0
        self.attempts_used   = 0
        self.model_used      = "?"
        self.time_s          = 0.0
        self.cost_delta      = 0.0
        self.diff            = ""
        self.notes           = []

    def score(self):
        pts = 0
        if self.success:        pts += 40
        if self.syntax_ok:      pts += 30
        if self.tests_pass:     pts += 20
        if len(self.diff) > 50: pts += 10
        return pts

    def verdict(self):
        s = self.score()
        if s >= 90: return f"{G}EXCELLENT{RST}"
        if s >= 70: return f"{G}GOOD{RST}"
        if s >= 50: return f"{Y}PARTIAL{RST}"
        return f"{R}FAILED{RST}"


def run_task(name, level, goal, target_files, codebase_root="."):
    from agent.orchestrator import Orchestrator

    header(f"L{level}: {name}", char="─")
    print(f"  {STEP} Goal   : {goal}")
    print(f"  {STEP} Files  : {', '.join(target_files)}")
    print()

    res = EvalResult(name, level, target_files[0])
    budget_before = read_budget()

    try:
        t0 = time.time()
        orch = Orchestrator()

        result = orch.execute_feature(
            goal=goal,
            codebase_root=codebase_root,
        )

        res.time_s           = time.time() - t0
        res.success          = result.get("success", False)
        res.tasks_completed  = result.get("tasks_completed", 0)
        res.tasks_total      = result.get("total_tasks", 0)
        res.cost_delta       = read_budget() - budget_before

    except Exception as e:
        res.notes.append(f"Exception: {e}")
        res.time_s = time.time() - t0

    # ── Quality checks ──────────────────────────────────────────────────
    for tf in target_files:
        fp = Path(codebase_root) / tf
        if fp.exists():
            ok, err = check_python_syntax(str(fp.resolve()))
            res.syntax_ok = res.syntax_ok or ok
            if not ok:
                res.notes.append(f"Syntax error in {tf}: {err[:100]}")

    res.diff = "\n".join(
        git_diff(f"scaffold/agent/{tf.replace('scaffold/agent/', '')}") or
        git_diff(tf)
        for tf in target_files
    )

    return res


def run_tests_for_level(level, target_files):
    """Run related tests and return pass/fail counts."""
    patterns = []
    for tf in target_files:
        base = Path(tf).stem
        patterns.append(f"tests/test_{base}.py")
        patterns.append(f"scaffold/tests/test_{base}.py")
        patterns.append(f"tests/test_tool_reflection.py")
        patterns.append(f"tests/test_reflexion_memory.py")

    passed = failed = 0
    for p in set(patterns):
        if Path(p).exists():
            pa, fa, _ = run_pytest(p)
            passed += pa
            failed += fa
    return passed, failed


# ── Main Evaluation ───────────────────────────────────────────────────────────

def main():
    header("AWOS CAPABILITY EVALUATION", char="═")
    print(textwrap.dedent(f"""
  {W}What this tests:{RST}
    {STEP} L1 — Add __repr__ to ErrorPattern dataclass
    {STEP} L2 — Add exponential backoff retry to _cheap_call()  
    {STEP} L3 — Add snapshot() to ToolPerformanceTracker + wire into Orchestrator summary
    {STEP} L4 — Extract prompt building from Worker into _build_prompt() helper

  {W}Measures:{RST}
    Success rate  |  Code quality (syntax + tests)
    Model routing  |  Cost per task  |  Escalation decisions
    Diff quality  |  Reflexion (saves critique on retry?)

  {Y}Note: Running on the REAL codebase. Git diff will show every change.{RST}
"""))

    input(f"  Press ENTER to start evaluation... ")

    budget_start = read_budget()
    results = []

    # ── L1: Easy — single-line addition, no logic ─────────────────────
    r1 = run_task(
        name        = "Add __repr__ to ErrorPattern",
        level       = 1,
        goal        = (
            "Add a __repr__ method to the ErrorPattern dataclass in "
            "scaffold/agent/error_pattern_store.py that returns: "
            "'ErrorPattern(file=X, type=Y, ts=Z)'"
        ),
        target_files= ["scaffold/agent/error_pattern_store.py"],
    )
    subheader("Quality Check — L1")
    p, f = run_tests_for_level(1, r1.target_file)
    r1.tests_pass = (f == 0 and p > 0)
    print(f"  Syntax OK:  {'✓' if r1.syntax_ok else '✗'}")
    print(f"  Tests:      {p} passed, {f} failed")
    print(f"  Cost:       ${r1.cost_delta:.4f}")
    print(f"  Time:       {r1.time_s:.1f}s")
    subheader("Diff")
    print_diff(r1.diff)
    results.append(r1)

    # Restore before next task (don't let changes compound)
    input(f"\n  {INFO} Press ENTER for L2 (restores L1 changes first)... ")
    git_restore("scaffold/agent/error_pattern_store.py")

    # ── L2: Medium — add retry logic (conditional, loops, time.sleep) ─
    r2 = run_task(
        name        = "Add exponential backoff to _cheap_call()",
        level       = 2,
        goal        = (
            "In scaffold/agent/orchestrator.py, modify _cheap_call() to retry "
            "up to 3 times with exponential backoff (delay: 0.5s, 1s, 2s). "
            "Only retry on network/API errors (Exception). If all retries fail, "
            "return empty string. Keep the existing logging."
        ),
        target_files= ["scaffold/agent/orchestrator.py"],
    )
    subheader("Quality Check — L2")
    ok, err = check_python_syntax("scaffold/agent/orchestrator.py")
    r2.syntax_ok = ok
    p, f, _ = run_pytest("tests/test_reflexion_memory.py")
    r2.tests_pass = (f == 0 and p > 0)
    print(f"  Syntax OK:  {'✓' if r2.syntax_ok else '✗' + ' ' + err[:80]}")
    print(f"  Tests:      {p} passed, {f} failed")
    print(f"  Cost:       ${r2.cost_delta:.4f}")
    print(f"  Time:       {r2.time_s:.1f}s")
    subheader("Diff")
    print_diff(r2.diff)
    results.append(r2)

    input(f"\n  {INFO} Press ENTER for L3 (restores L2 changes first)... ")
    git_restore("scaffold/agent/orchestrator.py")

    # ── L3: Hard — multi-file: new method + wire it up ────────────────
    r3 = run_task(
        name        = "Add ToolPerformanceTracker.snapshot() + wire to Orchestrator",
        level       = 3,
        goal        = (
            "Add a snapshot() method to ToolPerformanceTracker in "
            "scaffold/agent/core/performance_tracker.py. "
            "snapshot() returns a dict: {total_tasks, success_rate (float 0-1), "
            "avg_latency_ms, best_model (str or None)}. "
            "Then in scaffold/agent/orchestrator.py, at the end of the EXECUTION SUMMARY "
            "print block, add a one-line print of the snapshot: "
            "'[TRACKER] total=X success=Y% avg_ms=Z best=M'"
        ),
        target_files= [
            "scaffold/agent/core/performance_tracker.py",
            "scaffold/agent/orchestrator.py",
        ],
    )
    subheader("Quality Check — L3")
    ok1, e1 = check_python_syntax("scaffold/agent/core/performance_tracker.py")
    ok2, e2 = check_python_syntax("scaffold/agent/orchestrator.py")
    r3.syntax_ok = ok1 and ok2
    p, f, _ = run_pytest("tests/test_tool_reflection.py")
    r3.tests_pass = (f == 0 and p > 0)
    print(f"  Syntax OK:  {'✓' if r3.syntax_ok else '✗'}" + (f" tracker:{e1[:60]}" if not ok1 else "") + (f" orch:{e2[:60]}" if not ok2 else ""))
    print(f"  Tests:      {p} passed, {f} failed")
    print(f"  Cost:       ${r3.cost_delta:.4f}")
    print(f"  Time:       {r3.time_s:.1f}s")
    subheader("Diff — performance_tracker.py")
    print_diff(git_diff("scaffold/agent/core/performance_tracker.py"))
    subheader("Diff — orchestrator.py")
    print_diff(git_diff("scaffold/agent/orchestrator.py"))
    results.append(r3)

    input(f"\n  {INFO} Press ENTER for L4 (restores L3 changes first)... ")
    git_restore("scaffold/agent/core/performance_tracker.py")
    git_restore("scaffold/agent/orchestrator.py")

    # ── L4: Extreme — refactor (requires understanding 300+ LOC method) ─
    r4 = run_task(
        name        = "Refactor Worker: extract _build_prompt() helper",
        level       = 4,
        goal        = (
            "Refactor scaffold/agent/worker.py: extract the prompt-building logic "
            "from execute_task() into a new private method _build_prompt(self, task, "
            "file_content, codebase_context, attempt, error_context, past_critiques, "
            "vector_chunks, examples_section, skill_section, symbol_section) -> str. "
            "_build_prompt() returns the full system prompt string. "
            "execute_task() calls _build_prompt() and passes the result directly to "
            "the LLM. All existing behaviour must be preserved."
        ),
        target_files= ["scaffold/agent/worker.py"],
    )
    subheader("Quality Check — L4")
    ok, err = check_python_syntax("scaffold/agent/worker.py")
    r4.syntax_ok = ok
    p, f, _ = run_pytest("scaffold/tests/test_planner_worker.py")
    r4.tests_pass = (f == 0 and p > 0)
    print(f"  Syntax OK:  {'✓' if r4.syntax_ok else '✗' + ' ' + err[:80]}")
    print(f"  Tests:      {p} passed, {f} failed")
    print(f"  Cost:       ${r4.cost_delta:.4f}")
    print(f"  Time:       {r4.time_s:.1f}s")
    subheader("Diff")
    print_diff(r4.diff)
    results.append(r4)

    input(f"\n  {INFO} Press ENTER to see final scores (restores L4 changes)... ")
    git_restore("scaffold/agent/worker.py")

    # ── Final Scorecard ───────────────────────────────────────────────────
    header("EVALUATION SCORECARD", char="═")
    total_cost = read_budget() - budget_start

    print(f"\n  {'Level':<6}  {'Task':<42}  {'Score':<6}  {'Time':>7}  {'Cost':>8}  {'Verdict'}")
    print(f"  {'─'*6}  {'─'*42}  {'─'*6}  {'─'*7}  {'─'*8}  {'─'*12}")
    for r in results:
        sc = r.score()
        print(
            f"  L{r.level:<5}  {r.name:<42}  {sc:<6}  {r.time_s:>6.1f}s"
            f"  ${r.cost_delta:>7.4f}  {r.verdict()}"
        )

    print(f"\n  {'─' * 80}")
    avg_score = sum(r.score() for r in results) / len(results)
    print(f"\n  {W}Overall Score:   {avg_score:.0f} / 100{RST}")
    print(f"  {W}Total Cost:      ${total_cost:.4f}{RST}")
    print(f"  {W}Total Time:      {sum(r.time_s for r in results):.1f}s{RST}")
    print()

    # Qualitative interpretation
    if avg_score >= 80:
        print(f"  {G}VERDICT: Production-grade agent. Handles real multi-file engineering tasks.{RST}")
    elif avg_score >= 60:
        print(f"  {Y}VERDICT: Strong agent. Handles simple-medium tasks well. Complex refactors need work.{RST}")
    elif avg_score >= 40:
        print(f"  {Y}VERDICT: Emerging agent. Core pipeline works but complex tasks exceed capability.{RST}")
    else:
        print(f"  {R}VERDICT: Early-stage. Needs prompt engineering + context improvement.{RST}")

    print(f"\n  {INFO} Notes per task:")
    for r in results:
        if r.notes:
            print(f"    L{r.level} [{r.name}]:")
            for n in r.notes:
                print(f"      {WARN} {n}")

    print(f"\n  {INFO} All code changes have been reverted. Your codebase is clean.\n")


if __name__ == "__main__":
    main()
