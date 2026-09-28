"""Job 10: `report --json` (same numbers as the text report, integer cents, usual errors)."""

import io
import json
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
NOW = "2026-03-10T19:00:00Z"   # 00:30 on 2026-03-11 in Kolkata (+05:30)
KEYS = {"customer_id", "customer", "day", "timezone", "currency", "orders",
        "total_cents", "refunded_cents", "average_cents", "order_ids"}


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "s.db")
    with Storage(path) as storage:
        SettingsStore(storage).register("mumbai", "Mumbai Mangoes", "IST", "INR")
        storage.add_orders([
            Order("i0", "mumbai", 700, datetime(2026, 3, 10, 18, 0, tzinfo=UTC)),    # 23:30 on the 10th
            Order("i1", "mumbai", 10, datetime(2026, 3, 10, 18, 45, tzinfo=UTC)),    # 00:15 on the 11th
            Order("i2", "mumbai", 15, datetime(2026, 3, 11, 10, 0, tzinfo=UTC)),
            Order("i3", "mumbai", 500, datetime(2026, 3, 11, 11, 0, tzinfo=UTC), status="refunded"),
            Order("i4", "mumbai", 600, datetime(2026, 3, 11, 12, 0, tzinfo=UTC), status="cancelled"),
        ])
    return path


def run(*argv):
    out = io.StringIO()
    code = main(list(argv), out)
    return code, out.getvalue()


def report_json(db, *extra):
    code, out = run("--db", db, "--now", NOW, "report", "mumbai", *extra, "--json")
    assert code == 0, out
    return json.loads(out)


def test_today_in_the_merchants_zone(db):
    data = report_json(db)
    assert data == {
        "customer_id": "mumbai",
        "customer": "Mumbai Mangoes",
        "day": "2026-03-11",
        "timezone": "Asia/Kolkata",
        "currency": "INR",
        "orders": 2,
        "total_cents": 25,
        "refunded_cents": 500,
        "average_cents": 13,        # 12.5 rounds half up, like the text report
        "order_ids": ["i1", "i2"],
    }


def test_amounts_are_integer_cents(db):
    data = report_json(db)
    for key in ("orders", "total_cents", "refunded_cents", "average_cents"):
        assert type(data[key]) is int


def test_yesterday_and_day(db):
    data = report_json(db, "--yesterday")
    assert (data["day"], data["orders"], data["total_cents"], data["average_cents"], data["order_ids"]) == (
        "2026-03-10", 1, 700, 700, ["i0"])
    assert report_json(db, "--day", "2026-03-11") == report_json(db)


def test_same_numbers_as_the_text_report(db):
    code, text = run("--db", db, "--now", NOW, "report", "mumbai")
    assert code == 0
    assert "Average:   ₹0.13" in text and "Total:     ₹0.25" in text
    assert set(report_json(db)) == KEYS


def test_errors_stay_the_usual_error_line(db):
    assert run("--db", db, "report", "nobody", "--json") == (2, "error: unknown customer nobody\n")
    code, out = run("--db", db, "report", "mumbai", "--day", "2026-02-30", "--json")
    assert code == 2 and out.startswith("error: ") and out.count("\n") == 1
