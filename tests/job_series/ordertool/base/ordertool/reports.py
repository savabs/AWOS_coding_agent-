"""Plain-text summary reports."""

from collections import OrderedDict
from decimal import Decimal

from .models import OrderStatus
from .utils.dates import format_day
from .utils.money import format_money


def revenue_by_status(orders):
    """Return an ordered mapping status -> (order count, revenue)."""
    summary = OrderedDict((status, [0, Decimal("0.00")]) for status in OrderStatus)
    for order in orders:
        bucket = summary[order.status]
        bucket[0] += 1
        bucket[1] += order.total
    return OrderedDict((k, tuple(v)) for k, v in summary.items())


def daily_totals(orders):
    """Return an ordered mapping ``YYYY-MM-DD`` -> revenue, excluding cancelled orders."""
    totals = {}
    for order in orders:
        if order.status == OrderStatus.CANCELLED:
            continue
        day = format_day(order.created_at)
        totals[day] = totals.get(day, Decimal("0.00")) + order.total
    return OrderedDict(sorted(totals.items()))


def render_summary(orders):
    """Render a human-readable summary of ``orders``."""
    lines = [f"Orders: {len(orders)}", "", "By status:"]
    for status, (count, revenue) in revenue_by_status(orders).items():
        lines.append(f"  {status.value:<10} {count:>4}  {format_money(revenue):>10}")
    days = daily_totals(orders)
    if days:
        lines.append("")
        lines.append("Revenue by day (excluding cancelled):")
        for day, revenue in days.items():
            lines.append(f"  {day}  {format_money(revenue):>10}")
    return "\n".join(lines)
