"""ReportService — the public API used by the CLI and the web layer."""

from __future__ import annotations

from datetime import date, timedelta
from typing import List, Optional

from . import aggregation
from .clock import Clock, SystemClock
from .models import DailyReport, DayTotal
from .settings import SettingsStore
from .storage import Storage
from .timeutil import today, week_start


class ReportService:
    def __init__(self, storage: Storage, settings: Optional[SettingsStore] = None,
                 clock: Optional[Clock] = None) -> None:
        self.storage = storage
        self.settings = settings or SettingsStore(storage)
        self.clock = clock or SystemClock()

    def current_day(self, customer_id: str) -> date:
        """The merchant's current business day."""
        self.settings.get(customer_id)  # validate the customer exists
        return today(self.clock)

    def daily_report(self, customer_id: str, day: Optional[date] = None) -> DailyReport:
        """Report for ``day``; defaults to the merchant's current day."""
        customer = self.settings.get(customer_id)
        if day is None:
            day = self.current_day(customer_id)
        return aggregation.daily_report(self.storage, customer, day)

    def yesterday_report(self, customer_id: str) -> DailyReport:
        day = self.current_day(customer_id) - timedelta(days=1)
        return self.daily_report(customer_id, day)

    def breakdown(self, customer_id: str, first: date, last: date) -> List[DayTotal]:
        customer = self.settings.get(customer_id)
        return aggregation.daily_breakdown(self.storage, customer, first, last)

    def week_to_date(self, customer_id: str) -> List[DayTotal]:
        current = self.current_day(customer_id)
        return self.breakdown(customer_id, week_start(current), current)
