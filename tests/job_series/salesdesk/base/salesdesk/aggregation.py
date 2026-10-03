"""Per-day aggregation of orders."""

from __future__ import annotations

from datetime import date
from typing import Dict, List

from .models import Customer, DailyReport, DayTotal, Order
from .storage import Storage
from .timeutil import day_bounds, iter_days, local_date


def _summarise(orders: List[Order]) -> Dict[str, int]:
    total = 0
    count = 0
    refunded = 0
    for order in orders:
        if order.counts_toward_sales:
            total += order.amount_cents
            count += 1
        elif order.status == "refunded":
            refunded += order.amount_cents
        # cancelled orders are ignored entirely
    return {"total": total, "count": count, "refunded": refunded}


def orders_for_day(storage: Storage, customer: Customer, day: date) -> List[Order]:
    start, end = day_bounds(day, customer.timezone)
    return storage.orders_between(customer.customer_id, start, end)


def daily_report(storage: Storage, customer: Customer, day: date) -> DailyReport:
    orders = orders_for_day(storage, customer, day)
    summary = _summarise(orders)
    return DailyReport(
        customer_id=customer.customer_id,
        customer_name=customer.name,
        day=day,
        timezone=customer.timezone,
        currency=customer.currency,
        total_cents=summary["total"],
        order_count=summary["count"],
        refunded_cents=summary["refunded"],
        order_ids=[o.order_id for o in orders if o.counts_toward_sales],
    )


def daily_breakdown(storage: Storage, customer: Customer,
                    first: date, last: date) -> List[DayTotal]:
    """One :class:`DayTotal` per day in ``[first, last]`` (inclusive)."""
    days = list(iter_days(first, last))
    start, _ = day_bounds(first, customer.timezone)
    _, end = day_bounds(last, customer.timezone)
    orders = storage.orders_between(customer.customer_id, start, end)

    buckets: Dict[date, List[Order]] = {d: [] for d in days}
    for order in orders:
        bucket = local_date(order.created_at, customer.timezone)
        if bucket in buckets:
            buckets[bucket].append(order)

    result = []
    for d in days:
        summary = _summarise(buckets[d])
        result.append(DayTotal(d, summary["total"], summary["count"], summary["refunded"]))
    return result
