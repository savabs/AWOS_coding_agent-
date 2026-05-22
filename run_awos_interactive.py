#!/usr/bin/env python3
"""
Interactive AWOS Runner — type a goal, watch the agent work.

Usage:
    python3 run_awos_interactive.py

Then type your goal (e.g. "refactor the orchestrator to be cleaner")
"""
import sys
import os

sys.path.insert(0, "scaffold")

# Auto-load .env if present
from dotenv import load_dotenv
if os.path.exists(".env"):
    load_dotenv(".env")

os.environ.setdefault("AWOS_MONTHLY_BUDGET", "20.0")

PASS = "\033[92m✓\033[0m"
WARN = "\033[93m⚠\033[0m"
FAIL = "\033[91m✗\033[0m"
INFO = "\033[94mℹ\033[0m"


def banner():
    print("\n" + "═" * 60)
    print("  AWOS — Interactive Coding Agent")
    print("  Target: " + os.path.abspath("."))
    print("═" * 60)


def check_env():
    """Check if required API keys are set."""
    anthropic = os.getenv("ANTHROPIC_API_KEY")
    deepseek = os.getenv("DEEPSEEK_API_KEY")

    print(f"\n{INFO} Environment checks:")
    if anthropic:
        print(f"  {PASS} ANTHROPIC_API_KEY — Planner + Sonnet available")
    else:
        print(f"  {FAIL} ANTHROPIC_API_KEY — Planner will fail (required)")

    if deepseek:
        print(f"  {PASS} DEEPSEEK_API_KEY — Cheap Worker available")
    else:
        print(f"  {WARN} DEEPSEEK_API_KEY — Will fall back to Anthropic (more expensive)")

    if not anthropic:
        print(f"\n{WARN} ANTHROPIC_API_KEY not found — Planner may fail.")
        print("   Set it: export ANTHROPIC_API_KEY='your-key'")
        print("   Or create .env file with ANTHROPIC_API_KEY=...")
    return True


def run_goal(goal: str, dry_run: bool = False, resume: bool = False):
    """Execute a single goal through the Orchestrator."""
    from agent.orchestrator import Orchestrator
    from agent.budget_ledger import get_ledger

    # Show current budget
    ledger = get_ledger()
    status = ledger.get_status()
    monthly_budget = float(os.getenv("AWOS_MONTHLY_BUDGET", "20.0"))
    print(f"\n{INFO} Budget: ${status['spent']:.4f} / ${monthly_budget:.2f} "
          f"({status['percent_used']:.1f}% used)")

    if status["percent_used"] >= 90:
        print(f"{WARN} Budget is at {status['percent_used']:.0f}% — consider waiting.")
        resp = input("   Continue anyway? [y/N]: ").strip().lower()
        if resp != "y":
            print("Cancelled.")
            return

    print(f"\n{'─' * 60}")
    print(f"  GOAL: {goal}")
    print(f"{'─' * 60}\n")

    if dry_run:
        print(f"{INFO} DRY RUN — no files will be modified.")
        print(f"   (In real run, the Orchestrator would:")
        print(f"    1. Plan tasks via Claude")
        print(f"    2. Execute each task via DeepSeek/Claude")
        print(f"    3. Verify and apply changes")
        print(f"    4. Git backup + rollback on failure)")
        return

    try:
        orch = Orchestrator()
        result = orch.execute_feature(
            goal=goal,
            codebase_root=".",
            resume=resume,
        )

        print(f"\n{'=' * 60}")
        print(f"  RESULT")
        print(f"{'=' * 60}")
        print(f"  Success:     {PASS if result['success'] else FAIL} {result['success']}")
        print(f"  Tasks:       {result['tasks_completed']}/{result['total_tasks']} completed")
        if result['tasks_failed'] > 0:
            print(f"  Failed:      {FAIL} {result['tasks_failed']}")
        print(f"  Time:        {result['time_elapsed']:.1f}s")

        if result.get("errors"):
            print(f"\n  Errors:")
            for e in result["errors"][:5]:
                print(f"    {FAIL} {e}")

        # Show modified files from git status
        git_status = result.get("git", {})
        modified = git_status.get("modified", [])
        if modified:
            print(f"\n  Files modified:")
            for f in modified[:10]:
                print(f"    {PASS} {f}")

        return result

    except KeyboardInterrupt:
        print(f"\n\n{WARN} Interrupted by user.")
    except Exception as exc:
        print(f"\n{FAIL} Execution failed: {exc}")
        import traceback
        traceback.print_exc()


def main():
    banner()

    if not check_env():
        sys.exit(1)

    print("\n" + "─" * 60)
    print("  Type your goal below. AWOS will plan, code, and verify.")
    print("  Examples:")
    print('    "add a docstring to every public method"')
    print('    "refactor the orchestrator to use dependency injection"')
    print('    "add type hints to all functions in budget_ledger.py"')
    print("  Type 'quit' or press Ctrl+C to exit.")
    print("─" * 60)

    while True:
        try:
            print()
            goal = input("🎯 Goal: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not goal or goal.lower() in ("quit", "exit", "q"):
            print("Goodbye!")
            break

        # Parse flags
        dry_run = goal.startswith("--dry")
        resume = "--resume" in goal
        if dry_run:
            goal = goal.replace("--dry", "").strip()
        if resume:
            goal = goal.replace("--resume", "").strip()

        run_goal(goal, dry_run=dry_run, resume=resume)

        print(f"\n{'─' * 60}")
        print("  Done. Type another goal, or 'quit' to exit.")
        print(f"{'─' * 60}")


if __name__ == "__main__":
    main()
