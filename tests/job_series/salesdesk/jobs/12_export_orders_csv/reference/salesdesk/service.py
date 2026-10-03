"""ReportService — the public API used by the CLI and the web layer."""

from __future__ import annotations

from datetime import date, timedelta
from typing import List, Optional, Tuple

from . import aggregation
from .clock import Clock, SystemClock
from .models import Customer, DailyReport, DayTotal, MonthTotal, Order
from .settings import SettingsStore
from .storage import Storage, UnknownOrder
from .timeutil import today, week_start


class ReportService:
    def __init__(self, storage: Storage, settings: Optional[SettingsStore] = None,
                 clock: Optional[Clock] = None) -> None:
        self.storage = storage
        self.settings = settings or SettingsStore(storage)
        self.clock = clock or SystemClock()

    def current_day(self, customer_id: str) -> date:
        """The merchant's current business day."""
        customer = self.settings.get(customer_id)
        return today(self.clock, customer.timezone)

    def daily_report(self, customer_id: str, day: Optional[date] = None) -> DailyReport:
        """Report for ``day``; defaults to the merchant's current day."""
        customer = self.settings.get(customer_id)
        if day is None:
            day = self.current_day(customer_id)
        return aggregation.daily_report(self.storage, customer, day)

    def yesterday_report(self, customer_id: str) -> DailyReport:
        day = self.current_day(customer_id) - timedelta(days=1)
        return self.daily_report(customer_id, day)

    def day_orders(self, customer_id: str, day: Optional[date] = None) -> Tuple[Customer, date, List[Order]]:
        """All orders (any status) of the merchant's ``day``; defaults to their current day."""
        customer = self.settings.get(customer_id)
        if day is None:
            day = self.current_day(customer_id)
        return customer, day, aggregation.orders_for_day(self.storage, customer, day)

    def refund(self, order_id: str) -> Tuple[Order, Customer]:
        """Mark a paid order as refunded; returns the order (as it was) and its customer."""
        order = self.storage.get_order(order_id)
        if order is None:
            raise UnknownOrder(order_id)
        if order.status != "paid":
            raise ValueError(f"order {order_id} is {order.status}; only paid orders can be refunded")
        customer = self.settings.get(order.customer_id)
        self.storage.update_status(order_id, "refunded")
        return order, customer

    def orders_between_days(self, customer_id: str, first: date,
                            last: date) -> Tuple[Customer, List[Order]]:
        customer = self.settings.get(customer_id)
        return customer, aggregation.orders_for_days(self.storage, customer, first, last)

    def breakdown(self, customer_id: str, first: date, last: date) -> List[DayTotal]:
        customer = self.settings.get(customer_id)
        return aggregation.daily_breakdown(self.storage, customer, first, last)

    def monthly(self, customer_id: str, year: int) -> Tuple[Customer, List[MonthTotal]]:
        customer = self.settings.get(customer_id)
        return customer, aggregation.monthly_totals(self.storage, customer, year)

    def week_to_date(self, customer_id: str) -> List[DayTotal]:
        current = self.current_day(customer_id)
        return self.breakdown(customer_id, week_start(current), current)
