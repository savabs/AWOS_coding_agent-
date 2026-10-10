"""Rendering of reports for humans (text) and spreadsheets (CSV)."""

from __future__ import annotations

import csv
import io
import json
from typing import List

from .currency import divide_cents, format_cents
from .models import Customer, DailyReport, DayTotal, MonthTotal, Order
from .timeutil import format_local


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


def render_daily_json(report: DailyReport) -> str:
    """The daily report for machines: amounts in integer cents, like the CSV outputs."""
    data = {
        "customer_id": report.customer_id,
        "customer": report.customer_name,
        "day": report.day.isoformat(),
        "timezone": report.timezone,
        "currency": report.currency,
        "orders": report.order_count,
        "total_cents": report.total_cents,
        "refunded_cents": report.refunded_cents,
        "average_cents": report.average_order_cents,
        "order_ids": list(report.order_ids),
    }
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


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


def render_order_lines(orders: List[Order], customer: Customer) -> str:
    """One line per order: id, local time, amount, status."""
    if not orders:
        return "no orders\n"
    lines = [
        f"{o.order_id}  {format_local(o.created_at, customer.timezone)}  "
        f"{format_cents(o.amount_cents, customer.currency)}  {o.status}"
        for o in orders
    ]
    return "\n".join(lines) + "\n"


def render_monthly_text(rows: List[MonthTotal], currency: str = "USD") -> str:
    grand = sum(r.total_cents for r in rows)
    count = sum(r.order_count for r in rows)
    grand_avg = divide_cents(grand, count)
    amounts = [format_cents(v, currency) for r in rows for v in (r.total_cents, r.average_order_cents)]
    amounts += [format_cents(grand, currency), format_cents(grand_avg, currency)]
    width = max([len(a) for a in amounts] + [7])
    out = [f"{'month':<7}  {'orders':>6}  {'total':>{width}}  {'average':>{width}}"]
    for row in rows:
        out.append(
            f"{row.month:<7}  {row.order_count:>6}  "
            f"{format_cents(row.total_cents, currency):>{width}}  "
            f"{format_cents(row.average_order_cents, currency):>{width}}"
        )
    out.append(f"{'TOTAL':<7}  {count:>6}  {format_cents(grand, currency):>{width}}  "
               f"{format_cents(grand_avg, currency):>{width}}")
    return "\n".join(out) + "\n"


def render_monthly_csv(rows: List[MonthTotal]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["month", "order_count", "total_cents", "refunded_cents", "average_cents"])
    for row in rows:
        writer.writerow([row.month, row.order_count, row.total_cents, row.refunded_cents,
                         row.average_order_cents])
    return buf.getvalue()


def render_orders_csv(orders: List[Order], customer: Customer) -> str:
    """Individual orders for spreadsheets: local time, integer cents."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["order_id", "local_time", "amount_cents", "status"])
    for o in orders:
        writer.writerow([o.order_id, format_local(o.created_at, customer.timezone),
                         o.amount_cents, o.status])
    return buf.getvalue()
