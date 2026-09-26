"""Rendering of reports for humans (text) and spreadsheets (CSV)."""

from __future__ import annotations

import csv
import io
from typing import List

from .currency import format_cents
from .models import DailyReport, DayTotal


def render_daily_text(report: DailyReport) -> str:
    lines = [
        f"Daily sales — {report.customer_name}",
        f"Day:       {report.day.isoformat()} ({report.timezone})",
        f"Orders:    {report.order_count}",
        f"Total:     {format_cents(report.total_cents, report.currency)}",
        f"Average:   {format_cents(report.average_order_cents, report.currency)}",
    ]
    if report.refunded_cents:
        lines.append(f"Refunded:  {format_cents(report.refunded_cents, report.currency)}")
    return "\n".join(lines) + "\n"


def render_breakdown_text(rows: List[DayTotal], currency: str = "USD") -> str:
    width = max([len(format_cents(r.total_cents, currency)) for r in rows] + [5])
    out = [f"{'day':<10}  {'orders':>6}  {'total':>{width}}"]
    grand = 0
    count = 0
    for row in rows:
        out.append(
            f"{row.day.isoformat():<10}  {row.order_count:>6}  "
            f"{format_cents(row.total_cents, currency):>{width}}"
        )
        grand += row.total_cents
        count += row.order_count
    out.append(f"{'TOTAL':<10}  {count:>6}  {format_cents(grand, currency):>{width}}")
    return "\n".join(out) + "\n"


def render_breakdown_csv(rows: List[DayTotal]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["day", "order_count", "total_cents", "refunded_cents"])
    for row in rows:
        writer.writerow([row.day.isoformat(), row.order_count, row.total_cents, row.refunded_cents])
    return buf.getvalue()
