"""Plain-text summary reports."""

from collections import OrderedDict
from decimal import Decimal

from .models import OrderStatus
from .utils.dates import format_day
from .utils.money import HUNDRED, format_money, round_money


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


def customer_totals(orders):
    """Aggregate non-cancelled orders per customer.

    Customers are keyed by lower-cased email. Returns a list of
    ``(name, email, order_count, total)`` tuples sorted by total (highest
    first), then email. The name is taken from the customer's most recent
    order. Customers with only cancelled orders are omitted.
    """
    buckets = {}
    for order in orders:
        if order.status == OrderStatus.CANCELLED:
            continue
        email = order.email.strip().lower()
        bucket = buckets.setdefault(email, {"latest": None, "count": 0, "total": Decimal("0.00")})
        bucket["count"] += 1
        bucket["total"] += order.total
        if bucket["latest"] is None or order.created_at >= bucket["latest"].created_at:
            bucket["latest"] = order
    rows = [
        (b["latest"].customer, email, b["count"], b["total"]) for email, b in buckets.items()
    ]
    rows.sort(key=lambda row: (-row[3], row[1]))
    return rows


def monthly_totals(orders):
    """Return ``[(YYYY-MM, order_count, revenue), ...]`` oldest month first.

    Cancelled orders are ignored; months without orders are omitted.
    Revenue uses each order's (discounted) total.
    """
    months = {}
    for order in orders:
        if order.status == OrderStatus.CANCELLED:
            continue
        month = order.created_at.strftime("%Y-%m")
        count, revenue = months.get(month, (0, Decimal("0.00")))
        months[month] = (count + 1, revenue + order.total)
    return [(month, count, revenue) for month, (count, revenue) in sorted(months.items())]


def product_totals(orders):
    """Return ``[(sku, units, order_count, revenue), ...]`` for non-cancelled orders.

    Revenue is each line's subtotal after its order's discount, rounded to
    the cent per line, then summed. Sorted by revenue (highest first), then SKU.
    """
    products = {}
    for order in orders:
        if order.status == OrderStatus.CANCELLED:
            continue
        factor = (HUNDRED - order.discount_pct) / HUNDRED
        for item in order.items:
            entry = products.setdefault(item.sku, {"units": 0, "orders": set(), "revenue": Decimal("0.00")})
            entry["units"] += item.quantity
            entry["orders"].add(order.id)
            entry["revenue"] += round_money(item.subtotal * factor)
    rows = [
        (sku, e["units"], len(e["orders"]), e["revenue"]) for sku, e in products.items()
    ]
    rows.sort(key=lambda row: (-row[3], row[0]))
    return rows


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
