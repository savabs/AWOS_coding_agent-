"""Monthly budget checks."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List

from fintrack.models import month_bounds, quantize

ZERO = Decimal("0.00")


@dataclass
class BudgetStatus:
    category: str
    budget: Decimal
    spent: Decimal
    remaining: Decimal
    status: str


def check_budgets(ledger, year: int, month: int,
                  near_threshold: Decimal = Decimal("0.8")) -> List[BudgetStatus]:
    start, end = month_bounds(year, month)
    budgets = ledger.get_budgets()
    sums = {}
    for txn in ledger.transactions(start=start, end=end):
        if txn.category in budgets:
            sums[txn.category] = sums.get(txn.category, ZERO) + txn.amount
    result = []
    for category in sorted(budgets):
        budget = budgets[category]
        spent = quantize(max(ZERO, -sums.get(category, ZERO)))
        if spent > budget:
            status = "over"
        elif spent >= Decimal(near_threshold) * budget:
            status = "near"
        else:
            status = "ok"
        result.append(BudgetStatus(category, budget, spent, quantize(budget - spent), status))
    return result
