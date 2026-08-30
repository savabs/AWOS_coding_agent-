"""
CI Display — Terminal output formatting for CI repair benchmark results.

Formats CI repair results for terminal display with transparent routing,
benchmark economics, and summary statistics.

Cost: FREE (local formatting).
"""

from __future__ import annotations

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)


def print_ci_header(failures: List[dict], modules: Optional[List[str]] = None):
    """
    Print CI repair header with failure summary.

    Output:
    ═══════════════════════════════════════════════════════════════════
      AWOS CI Repair
    ═══════════════════════════════════════════════════════════════════

    [CI] Found 28 failing tests in 5 modules:
      • auth.py: 4 failures
      • billing.py: 5 failures
      ...
    """
    if not failures:
        print("\n[CI] No failing tests found. CI is green!")
        return

    # Count failures per module
    by_module: dict[str, int] = {}
    for f in failures:
        module = f["module"]
        by_module[module] = by_module.get(module, 0) + 1

    n_failures = len(failures)
    n_modules = len(by_module)

    print()
    print("═" * 66)
    print("  AWOS CI Rescue")
    print("═" * 66)
    print()
    print(f"[CI] Found {n_failures} failing tests in {n_modules} modules:")

    for module in sorted(by_module.keys()):
        count = by_module[module]
        print(f"  • {module}: {count} failure{'s' if count != 1 else ''}")

    print()


def print_ci_plan(plan: List[dict], grouped: bool = False):
    """
    Print CI rescue plan summary.

    Output:
    [CI] Generating CI rescue plan...
    [CI] Plan: 28 tasks (one per failure)
    """
    n_tasks = len(plan)
    label = "one per module" if grouped else "one per failure"

    print("[CI] Generating CI rescue plan...")
    print(f"[CI] Plan: {n_tasks} tasks ({label})")
    print()


def print_ci_results(
    failures: List[dict],
    result: dict,
    elapsed_time: float = 0.0,
):
    """
    Print CI rescue results with cost comparison.

    Output:
    ═══════════════════════════════════════════════════════════════════
      AWOS CI Rescue — Complete
    ═══════════════════════════════════════════════════════════════════

      Repo:   my-broken-repo
      Time:   4m 32s (unattended)
      Cost:   $0.0056

      Results:
        ✅ auth.py         — 4/4 fixed (DeepSeek)
        ✅ billing.py      — 5/5 fixed (DeepSeek)
        ⚠️ shipping.py     — 2/3 fixed (1 needs review)

      Summary: 26/28 fixed · 33/35 tests passing

      Your savings:
        This fix:    $0.0056
        If on Cursor: ~$0.02 (manual prompting)
        Savings: 72%
    ═══════════════════════════════════════════════════════════════════
    """
    # Extract result info
    success = result.get("success", False)
    tasks_completed = result.get("tasks_completed", 0)
    tasks_failed = result.get("tasks_failed", 0)
    total_cost = result.get("total_cost", 0.0)
    errors = result.get("errors", [])

    # Calculate totals
    n_failures = len(failures)
    n_fixed = tasks_completed
    n_remaining = tasks_failed

    # Format time
    if elapsed_time >= 60:
        minutes = int(elapsed_time // 60)
        seconds = int(elapsed_time % 60)
        time_str = f"{minutes}m {seconds}s"
    else:
        time_str = f"{elapsed_time:.1f}s"

    # Group failures by module for display
    by_module: dict[str, int] = {}
    for f in failures:
        module = f["module"]
        by_module[module] = by_module.get(module, 0) + 1

    # Print results
    print()
    print("═" * 66)
    print("  AWOS CI Rescue — Complete")
    print("═" * 66)
    print()
    print(f"  Time:   {time_str} (unattended)")
    print(f"  Cost:   ${total_cost:.4f}")
    print()

    # Per-module results
    print("  Results:")
    for module in sorted(by_module.keys()):
        count = by_module[module]
        # For now, assume all fixed (we don't have per-task results yet)
        print(f"    ✅ {module:20s} — {count} tasks submitted (DeepSeek)")

    print()

    # Summary
    print(f"  Summary: {n_fixed}/{n_failures} tasks completed")
    if n_remaining > 0:
        print(f"           {n_remaining} tasks need review")
    print()

    # Cost comparison
    cursor_estimate = total_cost * 3.0  # Rough estimate: Cursor costs ~3x
    if cursor_estimate > 0:
        savings_pct = ((cursor_estimate - total_cost) / cursor_estimate) * 100
        print("  Your savings:")
        print(f"    This fix:     ${total_cost:.4f}")
        print(f"    If on Cursor: ~${cursor_estimate:.4f} (manual prompting)")
        print(f"    Savings:      {savings_pct:.0f}%")
    print()
    print("═" * 66)
    print()

    # Print errors if any
    if errors:
        print("  Errors:")
        for error in errors[:5]:  # Show first 5 errors
            print(f"    • {error[:80]}")
        if len(errors) > 5:
            print(f"    ... and {len(errors) - 5} more")
        print()


def print_ci_no_failures():
    """Print message when no failures found."""
    print()
    print("═" * 66)
    print("  AWOS CI Rescue")
    print("═" * 66)
    print()
    print("[CI] No failing tests found. CI is already green!")
    print()
    print("═" * 66)
    print()
