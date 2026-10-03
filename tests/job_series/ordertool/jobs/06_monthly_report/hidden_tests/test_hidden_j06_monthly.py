"""Job 06: `monthly` CSV report (money rounding, inclusive dates, shared CSV writer)."""

import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

import ordertool.export as export_mod  # noqa: E402
from ordertool.cli import main  # noqa: E402
from ordertool.models import LineItem, Order, OrderStatus  # noqa: E402
from ordertool.storage import OrderStore  # noqa: E402

HEADER = "month,orders,revenue,average_order\n"

SEED = [
    # id, created, status, items, discount
    ("ORD-0001", datetime(2024, 1, 31, 23, 59, 59), OrderStatus.PAID, ["WIDGET:1:1.25"], "0"),
    ("ORD-0002", datetime(2024, 1, 3, 8, 0), OrderStatus.SHIPPED, ["PEN:1:1.00"], "0"),
    ("ORD-0003", datetime(2024, 1, 15, 8, 0), OrderStatus.CANCELLED, ["BOOK:1:500.00"], "0"),
    ("ORD-0004", datetime(2024, 2, 1, 0, 0), OrderStatus.PENDING, ["WIDGET:1:10.00"], "0"),
    ("ORD-0005", datetime(2024, 2, 10, 12, 0), OrderStatus.PAID, ["WIDGET:1:10.00"], "0"),
    ("ORD-0006", datetime(2024, 2, 29, 18, 0), OrderStatus.DELIVERED, ["WIDGET:1:10.01"], "0"),
    ("ORD-0007", datetime(2024, 3, 5, 9, 0), OrderStatus.CANCELLED, ["BOOK:1:79.90"], "0"),
    ("ORD-0008", datetime(2023, 12, 24, 9, 0), OrderStatus.DELIVERED, ["GIFT:1:2.25"], "50"),
]


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "orders.csv")
    store = OrderStore(path)
    for order_id, created, status, items, discount in SEED:
        store.add(Order(id=order_id, customer="C", email="c@example.com", created_at=created, status=status,
                        items=[LineItem.from_spec(i) for i in items], discount_pct=discount))
    return path


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_full_report(db, capsys):
    code, out, err = run(capsys, "--db", db, "monthly")
    assert code == 0, err
    assert out == (
        HEADER
        + "2023-12,1,1.13,1.13\n"     # 2.25 at 50% off = 1.125 -> 1.13
        + "2024-01,2,2.25,1.13\n"     # 2.25 / 2 = 1.125 -> 1.13 (half up)
        + "2024-02,3,30.01,10.00\n"   # 30.01 / 3 = 10.0033 -> 10.00
    )


def test_inclusive_date_range(db, capsys):
    code, out, _ = run(capsys, "--db", db, "monthly", "--from", "2024-01-31", "--to", "2024-02-01")
    assert code == 0
    assert out == HEADER + "2024-01,1,1.25,1.25\n" + "2024-02,1,10.00,10.00\n"


def test_month_with_only_cancelled_orders_is_left_out(db, capsys):
    code, out, _ = run(capsys, "--db", db, "monthly", "--from", "2024-03-01")
    assert code == 0
    assert out == HEADER


def test_output_file(db, capsys, tmp_path):
    target = tmp_path / "monthly.csv"
    code, out, err = run(capsys, "--db", db, "monthly", "--output", str(target))
    assert code == 0, err
    assert out == ""
    assert err.strip() == f"exported 3 rows to {target}"
    with open(target, newline="", encoding="utf-8") as fh:
        assert fh.read().startswith(HEADER)


def test_output_errors_are_clean(db, capsys, tmp_path):
    code, _, err = run(capsys, "--db", db, "monthly", "--output", str(tmp_path / "nope" / "m.csv"))
    assert code == 1
    assert err.startswith("error: ")


def test_bad_date_is_clean_error(db, capsys):
    code, out, err = run(capsys, "--db", db, "monthly", "--to", "2024-02-30")
    assert code == 1
    assert err.startswith("error: invalid date")
    assert out == ""


def test_uses_the_shared_csv_writer(db, capsys, monkeypatch):
    original = export_mod.write_csv
    calls = []

    def spy(header, rows, stream):
        calls.append(list(header))
        return original(header, rows, stream)

    for name, module in list(sys.modules.items()):
        if (name == "ordertool" or name.startswith("ordertool.")) and getattr(module, "write_csv", None) is original:
            monkeypatch.setattr(module, "write_csv", spy)
    code, _, _ = run(capsys, "--db", db, "monthly")
    assert code == 0
    assert calls == [["month", "orders", "revenue", "average_order"]]
