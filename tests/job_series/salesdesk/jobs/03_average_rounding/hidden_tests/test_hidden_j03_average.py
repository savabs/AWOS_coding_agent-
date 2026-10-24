"""Job 03: averages round half up to the cent via currency.divide_cents."""

import io
import os
import sys
from datetime import date, datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from salesdesk import currency  # noqa: E402
from salesdesk.cli import main  # noqa: E402
from salesdesk.models import DailyReport, Order  # noqa: E402
from salesdesk.settings import SettingsStore  # noqa: E402
from salesdesk.storage import Storage  # noqa: E402

UTC = timezone.utc


@pytest.mark.parametrize("cents,count,expected", [
    (25, 2, 13),        # 12.5 -> 13
    (5, 2, 3),          # 2.5 -> 3 (round() would give 2)
    (7, 2, 4),          # 3.5 -> 4
    (10, 3, 3),         # 3.33 -> 3
    (20, 3, 7),         # 6.67 -> 7
    (4000, 2, 2000),
    (0, 5, 0),
    (0, 0, 0),
    (999, 0, 0),
    (2 * 10**17 + 1, 2, 10**17 + 1),  # exact, no float drift
])
def test_divide_cents(cents, count, expected):
    assert currency.divide_cents(cents, count) == expected


def _report(total, count):
    return DailyReport("c", "C", date(2026, 3, 10), "UTC", "USD", total, count)


def test_daily_report_average_rounds_half_up():
    assert _report(25, 2).average_order_cents == 13
    assert _report(5, 2).average_order_cents == 3
    assert _report(20, 3).average_order_cents == 7
    assert _report(0, 0).average_order_cents == 0


def test_report_command_shows_the_rounded_average(tmp_path):
    db = str(tmp_path / "s.db")
    with Storage(db) as storage:
        SettingsStore(storage).register("acme", "Acme", "Asia/Kolkata", "INR")
        storage.add_orders([
            # both on 2026-03-10 in Kolkata (+05:30)
            Order("o1", "acme", 10, datetime(2026, 3, 9, 18, 45, tzinfo=UTC)),
            Order("o2", "acme", 15, datetime(2026, 3, 10, 18, 0, tzinfo=UTC)),
            Order("o3", "acme", 99, datetime(2026, 3, 10, 18, 30, tzinfo=UTC)),  # 00:00 on the 11th
        ])
    out = io.StringIO()
    assert main(["--db", db, "report", "acme", "--day", "2026-03-10"], out) == 0
    text = out.getvalue()
    assert "Total:     ₹0.25" in text
    assert "Average:   ₹0.13" in text
