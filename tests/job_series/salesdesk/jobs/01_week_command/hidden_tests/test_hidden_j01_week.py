"""Job 01: `week CUSTOMER` = Monday..today of the merchant's own week, shown like `breakdown`."""

import io
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from salesdesk.cli import main  # noqa: E402
from salesdesk.models import Order  # noqa: E402
from salesdesk.settings import SettingsStore  # noqa: E402
from salesdesk.storage import Storage  # noqa: E402

UTC = timezone.utc


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "s.db")
    with Storage(path) as storage:
        settings = SettingsStore(storage)
        settings.register("acme", "Acme Ltd", "UTC", "USD")
        settings.register("kiwi", "Kiwi Kites", "Pacific/Auckland", "NZD")
        settings.register("la", "LA Lamps", "America/Los_Angeles", "USD")
        storage.add_orders([
            # acme (UTC); 2026-03-09 is a Monday
            Order("a1", "acme", 1000, datetime(2026, 3, 8, 23, 59, tzinfo=UTC)),
            Order("a2", "acme", 2500, datetime(2026, 3, 9, 0, 1, tzinfo=UTC)),
            Order("a3", "acme", 700, datetime(2026, 3, 10, 13, 0, tzinfo=UTC), status="refunded"),
            Order("a4", "acme", 1500, datetime(2026, 3, 11, 12, 0, tzinfo=UTC)),
            # kiwi: 19:00 UTC Sunday is 08:00 Monday in Auckland (NZDT, +13)
            Order("k1", "kiwi", 1500, datetime(2026, 3, 8, 19, 0, tzinfo=UTC)),
            Order("k2", "kiwi", 900, datetime(2026, 3, 8, 10, 0, tzinfo=UTC)),
            # la: 02:00 UTC Tuesday is 19:00 Monday in Los Angeles (PDT, -7)
            Order("l1", "la", 2000, datetime(2026, 3, 10, 2, 0, tzinfo=UTC)),
            Order("l2", "la", 300, datetime(2026, 3, 9, 6, 0, tzinfo=UTC)),
        ])
    return path


def run(*argv):
    out = io.StringIO()
    code = main(list(argv), out)
    return code, out.getvalue()


def test_week_to_date_csv_for_utc_merchant(db):
    code, out = run("--db", db, "--now", "2026-03-11T20:00:00Z", "week", "acme", "--csv")
    assert code == 0, out
    assert out == (
        "day,order_count,total_cents,refunded_cents\n"
        "2026-03-09,1,2500,0\n"
        "2026-03-10,0,0,700\n"
        "2026-03-11,1,1500,0\n"
    )


def test_week_to_date_text_matches_breakdown(db):
    code, week = run("--db", db, "--now", "2026-03-11T20:00:00Z", "week", "acme")
    assert code == 0, week
    code, breakdown = run("--db", db, "breakdown", "acme", "2026-03-09", "2026-03-11")
    assert week == breakdown
    assert week.splitlines()[-1].split() == ["TOTAL", "2", "$40.00"]


def test_monday_morning_in_auckland_is_a_new_week(db):
    # Still Sunday in UTC, already Monday 09:00 in Auckland.
    code, out = run("--db", db, "--now", "2026-03-08T20:00:00Z", "week", "kiwi", "--csv")
    assert code == 0, out
    assert out == "day,order_count,total_cents,refunded_cents\n2026-03-09,1,1500,0\n"


def test_monday_evening_in_los_angeles_text(db):
    # Already Tuesday in UTC, still Monday 20:00 in Los Angeles.
    code, out = run("--db", db, "--now", "2026-03-10T03:00:00Z", "week", "la")
    assert code == 0, out
    lines = out.splitlines()
    assert [ln.split() for ln in lines] == [
        ["day", "orders", "total"],
        ["2026-03-09", "1", "$20.00"],
        ["TOTAL", "1", "$20.00"],
    ]


def test_unknown_customer_is_the_usual_error(db):
    assert run("--db", db, "--now", "2026-03-11T20:00:00Z", "week", "nobody") == (
        2, "error: unknown customer nobody\n")
