"""Job 04: order-level percentage discount (money rounding helper, storage compatibility)."""

import os
import sys
from datetime import datetime
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from ordertool.cli import main  # noqa: E402
from ordertool.errors import StorageError  # noqa: E402
from ordertool.models import LineItem, Order, OrderStatus  # noqa: E402
from ordertool.storage import OrderStore  # noqa: E402

OLD_HEADER = "id,customer,email,status,created_at,items\n"


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def add(capsys, db, *extra, item="WIDGET:1:1.25", when="2024-03-10T10:00:00", email="ada@example.com"):
    return run(capsys, "--db", db, "add", "--customer", "Ada", "--email", email,
               "--item", item, "--created-at", when, *extra)


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "orders.csv")


def test_discounted_total_rounds_half_up(db, capsys):
    # 1.25 * 0.90 = 1.125 -> 1.13 (half up), not 1.12
    code, out, err = add(capsys, db, "--discount", "10")
    assert code == 0, err
    assert out.strip() == "created ORD-0001 (1.13)"
    order = OrderStore(db).get("ORD-0001")
    assert order.total == Decimal("1.13")


def test_decimal_percentage(db, capsys):
    # 2.25 * 0.5 = 1.125 -> 1.13 ; 29.97 * 0.875 = 26.22375 -> 26.22
    assert add(capsys, db, "--discount", "50", item="PEN:1:2.25")[0] == 0
    assert add(capsys, db, "--discount", "12.5", item="WIDGET:3:9.99")[0] == 0
    store = OrderStore(db)
    assert store.get("ORD-0001").total == Decimal("1.13")
    assert store.get("ORD-0002").total == Decimal("26.22")


def test_discount_survives_reload_and_status_change(db, capsys):
    assert add(capsys, db, "--discount", "10")[0] == 0
    assert run(capsys, "--db", db, "set-status", "ORD-0001", "paid")[0] == 0
    order = OrderStore(db).get("ORD-0001")
    assert order.status is OrderStatus.PAID
    assert order.total == Decimal("1.13")


def test_no_discount_by_default(db, capsys):
    code, out, _ = add(capsys, db, item="WIDGET:2:9.99")
    assert code == 0
    assert out.strip() == "created ORD-0001 (19.98)"


def test_list_export_and_customers_use_discounted_total(db, capsys):
    assert add(capsys, db, "--discount", "10")[0] == 0
    assert add(capsys, db, "--discount", "10", when="2024-03-11T10:00:00")[0] == 0
    code, out, _ = run(capsys, "--db", db, "list")
    assert code == 0
    assert out.splitlines()[0].split()[3] == "1.13"
    code, out, _ = run(capsys, "--db", db, "export")
    assert out.splitlines()[1].endswith(",1,1.13")
    code, out, _ = run(capsys, "--db", db, "customers")
    # the customer total is the sum of the two rounded order totals
    assert out.splitlines()[1] == "Ada,ada@example.com,2,2.26"
    code, out, _ = run(capsys, "--db", db, "report")
    assert "2.26" in out


@pytest.mark.parametrize("value", ["-5", "100.01", "150", "ten"])
def test_invalid_discount_is_clean_error(db, capsys, value):
    code, out, err = add(capsys, db, "--discount", value)
    assert code == 1
    assert err.startswith("error: ")
    assert OrderStore(db).load_all() == []


def test_full_discount_allowed(db, capsys):
    code, out, err = add(capsys, db, "--discount", "100")
    assert code == 0, err
    assert out.strip() == "created ORD-0001 (0.00)"


def test_old_orders_file_still_loads(tmp_path, capsys):
    path = tmp_path / "old.csv"
    path.write_text(
        OLD_HEADER
        + "ORD-0001,Ada,ada@example.com,paid,2024-03-01T09:00:00,WIDGET:2:9.99\n"
        + "ORD-0002,Grace,grace@example.com,pending,2024-03-02T09:00:00,GADGET:1:25.00|CABLE:3:4.50\n",
        encoding="utf-8",
    )
    orders = OrderStore(str(path)).load_all()
    assert [o.total for o in orders] == [Decimal("19.98"), Decimal("38.50")]
    # the tool keeps working on the old file (and may upgrade it on write)
    code, _, err = run(capsys, "--db", str(path), "set-status", "ORD-0002", "paid")
    assert code == 0, err
    code, out, _ = run(capsys, "--db", str(path), "add", "--customer", "Bob", "--email", "bob@example.com",
                       "--item", "PEN:1:2.25", "--discount", "50", "--created-at", "2024-03-03T09:00:00")
    assert code == 0
    assert [str(o.total) for o in OrderStore(str(path)).load_all()] == ["19.98", "38.50", "1.13"]


def test_unknown_columns_still_rejected(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("id,name,total\nORD-0001,Ada,1.00\n", encoding="utf-8")
    with pytest.raises(StorageError):
        OrderStore(str(path)).load_all()


def test_orders_built_in_code_default_to_no_discount():
    order = Order(id="ORD-0001", customer="Ada", email="ada@example.com",
                  created_at=datetime(2024, 3, 1), items=[LineItem.from_spec("WIDGET:2:9.99")])
    assert order.total == Decimal("19.98")
