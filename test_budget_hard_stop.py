#!/usr/bin/env python3
"""
Smoke test for BudgetLedger.check_budget() — block, warn, pass states.
No API keys required.
"""
import sys
import os
import tempfile

sys.path.insert(0, "scaffold")
sys.path.insert(0, "scaffold/agent")
os.environ.setdefault("AWOS_MONTHLY_BUDGET", "20.0")

from agent.budget_ledger import BudgetLedger

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results = []


def check(name, cond, detail=""):
    results.append((name, cond, detail))
    mark = PASS if cond else FAIL
    extra = f" — {detail}" if not cond and detail else ""
    print(f"  {mark} {name}{extra}")


print("=" * 60)
print("Budget Hard Stop Smoke Test")
print("=" * 60)

with tempfile.TemporaryDirectory() as tmp:
    # Test 1: check_budget blocks when over 100%
    print("\n1. Block at 100%")
    ledger = BudgetLedger(ledger_path=os.path.join(tmp, "budget.json"))
    ledger._monthly_total = 21.0  # force over budget
    allowed, reason = ledger.check_budget(0.01, monthly_budget=20.0)
    check("block_not_allowed", allowed is False, f"got {allowed}")
    check("block_has_reason", "BUDGET" in reason, f"got {reason!r}")
    check("block_says_exhausted", "EXHAUSTED" in reason, f"got {reason!r}")

    # Test 2: check_budget warns at 90%
    print("\n2. Warn at 90%")
    ledger2 = BudgetLedger(ledger_path=os.path.join(tmp, "budget2.json"))
    ledger2._monthly_total = 18.5  # 92.5%
    allowed2, reason2 = ledger2.check_budget(0.01, monthly_budget=20.0)
    check("warn_allowed", allowed2 is True, f"got {allowed2}")
    check("warn_reason_set", "WARNING" in reason2, f"got {reason2!r}")

    # Test 3: check_budget passes under 90%
    print("\n3. Pass under 90%")
    ledger3 = BudgetLedger(ledger_path=os.path.join(tmp, "budget3.json"))
    ledger3._monthly_total = 5.0
    allowed3, reason3 = ledger3.check_budget(0.01, monthly_budget=20.0)
    check("pass_allowed", allowed3 is True, f"got {allowed3}")
    check("pass_no_reason", reason3 == "", f"got {reason3!r}")

    # Test 4: check_budget blocks a call that would exceed
    print("\n4. Block if this call would exceed")
    ledger4 = BudgetLedger(ledger_path=os.path.join(tmp, "budget4.json"))
    ledger4._monthly_total = 19.5  # only 0.50 left
    allowed4, reason4 = ledger4.check_budget(1.0, monthly_budget=20.0)
    check("block_exceed_not_allowed", allowed4 is False, f"got {allowed4}")
    check("block_exceed_says_block", "BLOCK" in reason4, f"got {reason4!r}")

    # Test 5: budget_status reflects correct percentage
    print("\n5. Budget status math")
    ledger5 = BudgetLedger(ledger_path=os.path.join(tmp, "budget5.json"))
    ledger5._monthly_total = 15.0
    status = ledger5.get_status(monthly_budget=20.0)
    check("status_pct_75", status["percent_used"] == 75.0, f"got {status['percent_used']}")
    check("status_spent_15", status["spent"] == 15.0, f"got {status['spent']}")

    # Test 6: zero budget edge case
    print("\n6. Zero budget")
    ledger6 = BudgetLedger(ledger_path=os.path.join(tmp, "budget6.json"))
    ledger6._monthly_total = 0.0
    allowed6, reason6 = ledger6.check_budget(0.01, monthly_budget=0.0)
    check("zero_budget_blocked", allowed6 is False, f"got {allowed6}")

# ═════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
failed_count = sum(1 for _, ok, _ in results if not ok)
print(f"Total: {passed}/{len(results)} passed  |  {failed_count} failed")
if failed_count:
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL {name}: {detail}")
    sys.exit(1)
else:
    print("\nAll Budget Hard Stop tests PASSED.")
    sys.exit(0)
