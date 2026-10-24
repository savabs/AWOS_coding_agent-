"""Domain models for salesdesk."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List

ORDER_STATUSES = ("paid", "refunded", "cancelled")


@dataclass(frozen=True)
class Customer:
    """A merchant using salesdesk.

    ``timezone`` is an IANA zone name (e.g. ``"Europe/Berlin"``). Reports are
    meant to be expressed in the merchant's own calendar days.
    """

    customer_id: str
    name: str
    timezone: str = "UTC"
    currency: str = "USD"


@dataclass(frozen=True)
class Order:
    """A single order.

    ``created_at`` may be timezone-aware (any zone) or naive. Naive values are
    interpreted as UTC — that is also the form in which orders are stored and
    returned by :mod:`salesdesk.storage`.
    """

    order_id: str
    customer_id: str
    amount_cents: int
    created_at: datetime
    status: str = "paid"

    def __post_init__(self) -> None:
        if self.status not in ORDER_STATUSES:
            raise ValueError(f"unknown order status: {self.status!r}")
        if not isinstance(self.amount_cents, int):
            raise TypeError("amount_cents must be an int")
        if self.amount_cents < 0:
            raise ValueError("amount_cents must be non-negative")

    @property
    def counts_toward_sales(self) -> bool:
        return self.status == "paid"


@dataclass(frozen=True)
class DayTotal:
    """Totals for one calendar day."""

    day: date
    total_cents: int
    order_count: int
    refunded_cents: int = 0


@dataclass
class DailyReport:
    """The daily sales report shown to a merchant."""

    customer_id: str
    customer_name: str
    day: date
    timezone: str
    currency: str
    total_cents: int
    order_count: int
    refunded_cents: int = 0
    order_ids: List[str] = field(default_factory=list)

    @property
    def average_order_cents(self) -> int:
        if self.order_count == 0:
            return 0
        return self.total_cents // self.order_count
