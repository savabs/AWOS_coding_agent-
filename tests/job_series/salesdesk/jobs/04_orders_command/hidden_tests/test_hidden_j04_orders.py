"""Job 04: `orders CUSTOMER [--day D | --yesterday]` in the merchant's own day, clock and currency."""

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

TENTH = (
    "b1  2026-03-10 00:30  €12.50  paid\n"
    "b2  2026-03-10 09:01  €25.00  refunded\n"
    "b3  2026-03-10 13:00  €1,234.56  cancelled\n"
)


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "s.db")
    with Storage(path) as storage:
        SettingsStore(storage).register("berlin", "Berlin Bikes", "Europe/Berlin", "EUR")
        storage.add_orders([
            # Berlin is UTC+1 in early March
            Order("b3", "berlin", 123456, datetime(2026, 3, 10, 12, 0, tzinfo=UTC), status="cancelled"),
            Order("b0", "berlin", 100, datetime(2026, 3, 9, 22, 59, tzinfo=UTC)),
            Order("b1", "berlin", 1250, datetime(2026, 3, 9, 23, 30, tzinfo=UTC)),
            Order("b2", "berlin", 2500, datetime(2026, 3, 10, 8, 1, tzinfo=UTC), status="refunded"),
            Order("b4", "berlin", 300, datetime(2026, 3, 10, 23, 0, tzinfo=UTC)),
        ])
    return path


def run(*argv):
    out = io.StringIO()
    code = main(list(argv), out)
    return code, out.getvalue()


def test_orders_of_a_given_local_day(db):
    assert run("--db", db, "orders", "berlin", "--day", "2026-03-10") == (0, TENTH)


def test_default_is_the_merchants_today(db):
    # 23:30 UTC on the 10th is 00:30 on the 11th in Berlin.
    code, out = run("--db", db, "--now", "2026-03-10T23:30:00Z", "orders", "berlin")
    assert (code, out) == (0, "b4  2026-03-11 00:00  €3.00  paid\n")


def test_yesterday(db):
    code, out = run("--db", db, "--now", "2026-03-10T23:30:00Z", "orders", "berlin", "--yesterday")
    assert (code, out) == (0, TENTH)


def test_day_without_orders(db):
    assert run("--db", db, "orders", "berlin", "--day", "2026-03-01") == (0, "no orders\n")


def test_errors_are_the_usual_ones(db):
    assert run("--db", db, "orders", "nobody", "--day", "2026-03-10") == (2, "error: unknown customer nobody\n")
    code, out = run("--db", db, "orders", "berlin", "--day", "2026-02-30")
    assert code == 2
    assert out.startswith("error: ") and out.count("\n") == 1
