"""Job 11: `summary [--day D]`: every merchant's own day, counted like the report, in their currency."""

import io
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from salesdesk import cli  # noqa: E402
from salesdesk.models import Order  # noqa: E402
from salesdesk.settings import SettingsStore  # noqa: E402
from salesdesk.storage import Storage  # noqa: E402

UTC = timezone.utc


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "s.db")
    with Storage(path) as storage:
        settings = SettingsStore(storage)
        # registered out of order on purpose
        settings.register("la", "LA Lamps", "America/Los_Angeles", "USD")
        settings.register("acme", "Acme Ltd", "UTC", "USD")
        settings.register("kiwi", "Kiwi Kites", "Pacific/Auckland", "NZD")
        storage.add_orders([
            Order("a1", "acme", 1000, datetime(2026, 3, 10, 1, 0, tzinfo=UTC)),
            Order("a2", "acme", 500, datetime(2026, 3, 10, 2, 0, tzinfo=UTC), status="refunded"),
            Order("a3", "acme", 999, datetime(2026, 3, 9, 23, 0, tzinfo=UTC)),
            Order("a4", "acme", 50, datetime(2026, 3, 10, 3, 0, tzinfo=UTC), status="cancelled"),
            Order("k1", "kiwi", 2500, datetime(2026, 3, 10, 11, 30, tzinfo=UTC)),   # 11th 00:30 local
            Order("k2", "kiwi", 777, datetime(2026, 3, 10, 10, 0, tzinfo=UTC)),     # 10th 23:00 local
            Order("l1", "la", 100, datetime(2026, 3, 10, 6, 59, tzinfo=UTC)),       # 9th 23:59 local
            Order("l2", "la", 123456, datetime(2026, 3, 10, 7, 0, tzinfo=UTC)),     # 10th 00:00 local
        ])
    return path


def run(*argv):
    out = io.StringIO()
    code = cli.main(list(argv), out)
    return code, out.getvalue()


def test_default_is_each_merchants_own_today(db):
    # 11:00 UTC on the 10th: already the 11th in Auckland, still the 10th elsewhere.
    assert run("--db", db, "--now", "2026-03-10T11:00:00Z", "summary") == (0, (
        "acme  2026-03-10  orders: 1  total: $10.00\n"
        "kiwi  2026-03-11  orders: 1  total: NZ$25.00\n"
        "la  2026-03-10  orders: 1  total: $1,234.56\n"
    ))


def test_given_day_is_each_merchants_local_day(db):
    assert run("--db", db, "summary", "--day", "2026-03-10") == (0, (
        "acme  2026-03-10  orders: 1  total: $10.00\n"
        "kiwi  2026-03-10  orders: 1  total: NZ$7.77\n"
        "la  2026-03-10  orders: 1  total: $1,234.56\n"
    ))


def test_no_customers(tmp_path):
    assert run("--db", str(tmp_path / "empty.db"), "summary") == (0, "no customers\n")


def test_bad_day_is_the_usual_error(db):
    code, out = run("--db", db, "summary", "--day", "2026-3-1x")
    assert code == 2 and out.startswith("error: ") and out.count("\n") == 1


def test_registered_like_every_other_command():
    args = cli.build_parser().parse_args(["summary"])
    assert args.handler is cli.cmd_summary
