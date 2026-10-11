"""Monthly per-category reports."""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List

from fintrack.models import fmt_money, month_bounds, quantize

UNCATEGORIZED = "Uncategorized"
ZERO = Decimal("0.00")


@dataclass
class CategoryRow:
    category: str
    count: int
    total: Decimal


@dataclass
class MonthlyReport:
    year: int
    month: int
    rows: List[CategoryRow]
    income: Decimal
    expenses: Decimal
    net: Decimal

    @property
    def label(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


def monthly_report(ledger, year: int, month: int) -> MonthlyReport:
    start, end = month_bounds(year, month)
    totals: Dict[str, Decimal] = {}
    counts: Dict[str, int] = {}
    income = expenses = ZERO
    for txn in ledger.transactions(start=start, end=end):
        cat = txn.category if txn.category is not None else UNCATEGORIZED
        totals[cat] = totals.get(cat, ZERO) + txn.amount
        counts[cat] = counts.get(cat, 0) + 1
        if txn.amount > 0:
            income += txn.amount
        else:
            expenses += txn.amount
    rows = [CategoryRow(cat, counts[cat], quantize(total)) for cat, total in totals.items()]
    rows.sort(key=lambda r: (r.total, r.category))
    income, expenses = quantize(income), quantize(expenses)
    return MonthlyReport(year, month, rows, income, expenses, quantize(income + expenses))


def _line(category, count, total) -> str:
    return f"{category:<20} {count:>5} {total:>12}"


def render_text(report: MonthlyReport) -> str:
    lines = [f"Report for {report.label}"]
    if not report.rows:
        lines.append("No transactions.")
    else:
        lines.append(_line("Category", "Count", "Total"))
        lines += [_line(r.category, r.count, fmt_money(r.total)) for r in report.rows]
        lines.append(f"Income: {fmt_money(report.income)}")
        lines.append(f"Expenses: {fmt_money(report.expenses)}")
        lines.append(f"Net: {fmt_money(report.net)}")
    return "\n".join(lines) + "\n"


def render_json(report: MonthlyReport) -> str:
    return json.dumps({
        "month": report.label,
        "categories": [
            {"category": r.category, "count": r.count, "total": fmt_money(r.total)}
            for r in report.rows
        ],
        "income": fmt_money(report.income),
        "expenses": fmt_money(report.expenses),
        "net": fmt_money(report.net),
    }, indent=2)
