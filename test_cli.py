#!/usr/bin/env python3
"""
CLI smoke test for awos.py — verifies argument parsing and handler wiring.
No API keys required. Uses dry-run / safe-read-only commands only.
"""
import sys
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).parent
AWOS = REPO / "awos.py"

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results = []


def run(cmd: list[str]) -> tuple[int, str]:
    env = os.environ.copy()
    env["TOKENIZERS_PARALLELISM"] = "false"
    env["AWOS_MONTHLY_BUDGET"] = "20.0"
    proc = subprocess.run(
        [sys.executable, str(AWOS)] + cmd,
        capture_output=True,
        text=True,
        cwd=REPO,
        env=env,
        timeout=60,
    )
    return proc.returncode, proc.stdout + proc.stderr


def assert_test(name, condition, detail=""):
    if condition:
        results.append((name, True, detail))
        print(f"  {PASS} {name}")
    else:
        results.append((name, False, detail))
        print(f"  {FAIL} {name} — {detail}")


print("=" * 60)
print("AWOS CLI Smoke Test")
print("=" * 60)

# ── 1. Help shows all subcommands ──────────────────────────────────────
print("\n1. Help output")
rc, out = run(["--help"])
assert_test("help_exit_0", rc == 0, f"exit={rc}")
assert_test("help_has_chat", "chat" in out)
assert_test("help_has_memory", "memory" in out)
assert_test("help_has_traces", "traces" in out)
assert_test("help_has_budget", "budget" in out)
assert_test("help_has_index", "index" in out)
assert_test("help_has_run", "run" in out)

# ── 2. Budget command ──────────────────────────────────────────────────
print("\n2. Budget")
rc, out = run(["budget"])
assert_test("budget_exit_0", rc == 0, f"exit={rc}")
assert_test("budget_has_spent", "Spent:" in out, out[:200])
assert_test("budget_has_requests", "Requests:" in out)

# ── 3. Memory stats ────────────────────────────────────────────────────
print("\n3. Memory stats")
rc, out = run(["memory", "stats"])
assert_test("mem_stats_exit_0", rc == 0, f"exit={rc}")
assert_test("mem_stats_has_total", "Total interactions:" in out, out[:200])

# ── 4. Memory search (no results is OK) ────────────────────────────────
print("\n4. Memory search")
rc, out = run(["memory", "search", "xyz_nonexistent_query", "-n", "1"])
assert_test("mem_search_exit_0", rc == 0, f"exit={rc}")

# ── 5. Traces list ─────────────────────────────────────────────────────
print("\n5. Traces list")
rc, out = run(["traces", "list"])
assert_test("traces_list_exit_0", rc == 0, f"exit={rc}")

# ── 6. Traces show (missing session) ───────────────────────────────────
print("\n6. Traces show (missing)")
rc, out = run(["traces", "show", "no_such_session"])
assert_test("traces_show_exit_0", rc == 0, f"exit={rc}")
assert_test("traces_show_not_found", "not found" in out.lower())

# ── 7. Traces clean (dry) ──────────────────────────────────────────────
print("\n7. Traces clean")
rc, out = run(["traces", "clean", "--days", "1"])
assert_test("traces_clean_exit_0", rc == 0, f"exit={rc}")

# ── 8. Index ───────────────────────────────────────────────────────────
print("\n8. Index")
rc, out = run(["index"])
assert_test("index_exit_0", rc == 0, f"exit={rc}")
assert_test("index_report", "Indexed" in out and "chunks" in out, out[:200])

# ════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)

passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)

for name, ok, detail in results:
    mark = PASS if ok else FAIL
    print(f"  {mark} {name}")
    if not ok and detail:
        print(f"      → {detail}")

print()
print(f"Total: {passed}/{len(results)} passed  |  {failed} failed")

if failed > 0:
    print("\nSome tests FAILED.")
    sys.exit(1)
else:
    print("\nAll CLI tests PASSED.")
    sys.exit(0)
