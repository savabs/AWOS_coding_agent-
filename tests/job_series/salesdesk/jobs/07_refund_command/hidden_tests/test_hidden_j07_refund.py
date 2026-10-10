"""Job 07: `refund ORDER_ID` (paid orders only, merchant's currency, usual errors, handler convention)."""

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
        settings.register("berlin", "Berlin Bikes", "Europe/Berlin", "EUR")
        settings.register("tokyo", "Tokyo Toys", "Asia/Tokyo", "JPY")
        storage.add_orders([
            Order("b1", "berlin", 1250, datetime(2026, 3, 10, 8, 0, tzinfo=UTC)),
            Order("b2", "berlin", 250075, datetime(2026, 3, 10, 9, 0, tzinfo=UTC)),
            Order("b3", "berlin", 500, datetime(2026, 3, 10, 10, 0, tzinfo=UTC), status="refunded"),
            Order("b4", "berlin", 700, datetime(2026, 3, 10, 11, 0, tzinfo=UTC), status="cancelled"),
            Order("t1", "tokyo", 150000, datetime(2026, 3, 10, 1, 0, tzinfo=UTC)),
        ])
    return path


def run(*argv):
    out = io.StringIO()
    code = cli.main(list(argv), out)
    return code, out.getvalue()


def status(db, order_id):
    with Storage(db) as storage:
        return storage.get_order(order_id).status


def test_refund_a_paid_order(db):
    assert run("--db", db, "refund", "b2") == (0, "refunded b2 (€2,500.75)\n")
    assert status(db, "b2") == "refunded"
    assert status(db, "b1") == "paid"


def test_amount_in_the_merchants_currency(db):
    assert run("--db", db, "refund", "t1") == (0, "refunded t1 (¥1,500)\n")


def test_report_reflects_the_refund(db):
    assert run("--db", db, "refund", "b1")[0] == 0
    code, out = run("--db", db, "report", "berlin", "--day", "2026-03-10")
    assert code == 0
    assert "Orders:    1" in out
    assert "Total:     €2,500.75" in out
    assert "Refunded:  €17.50" in out


def test_unknown_order_is_reported_like_an_unknown_customer(db):
    assert run("--db", db, "refund", "zz9") == (2, "error: unknown order zz9\n")


@pytest.mark.parametrize("order_id,was", [("b3", "refunded"), ("b4", "cancelled")])
def test_only_paid_orders_can_be_refunded(db, order_id, was):
    code, out = run("--db", db, "refund", order_id)
    assert code == 2
    assert out.startswith("error: ") and out.count("\n") == 1
    assert order_id in out
    assert status(db, order_id) == was


def test_registered_like_every_other_command():
    args = cli.build_parser().parse_args(["refund", "b1"])
    assert args.handler is cli.cmd_refund
