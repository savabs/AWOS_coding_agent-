"""CSV / JSON export with filters."""
from __future__ import annotations

import csv
import json

from fintrack.errors import ValidationError
from fintrack.models import fmt_money

EXPORT_COLUMNS = ["date", "description", "amount", "category", "account"]
EXPORT_FORMATS = ("csv", "json")


def export_transactions(ledger, out, fmt: str = "csv", start=None, end=None, category=None) -> int:
    if fmt not in EXPORT_FORMATS:
        raise ValidationError(f"unknown export format {fmt!r} (expected csv or json)")
    if start is not None and end is not None and start > end:
        raise ValidationError(f"start date {start} is after end date {end}")
    txns = ledger.transactions(start=start, end=end, category=category)
    if fmt == "csv":
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(EXPORT_COLUMNS)
        for t in txns:
            writer.writerow([t.date.isoformat(), t.description, fmt_money(t.amount),
                             t.category or "", t.account])
    else:
        json.dump([
            {"date": t.date.isoformat(), "description": t.description, "amount": fmt_money(t.amount),
             "category": t.category, "account": t.account}
            for t in txns
        ], out, indent=2)
        out.write("\n")
    return len(txns)
