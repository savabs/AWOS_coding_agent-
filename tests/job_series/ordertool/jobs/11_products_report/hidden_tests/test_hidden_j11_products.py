"""Job 11: `products` CSV report (discount share rounded per line, shared CSV conventions)."""

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

HEADER = "sku,units,orders,revenue\n"

SEED = [
    # id, created, status, items, discount
    ("ORD-0001", datetime(2024, 3, 1, 9, 0), OrderStatus.PAID, ["WIDGET:1:1.25", "PEN:2:3.00"], "10"),
    ("ORD-0002", datetime(2024, 3, 2, 9, 0), OrderStatus.SHIPPED, ["WIDGET:1:1.25"], "10"),
    ("ORD-0003", datetime(2024, 3, 3, 23, 59, 59), OrderStatus.PENDING, ["PEN:1:3.00", "PEN:1:2.00"], "0"),
    ("ORD-0004", datetime(2024, 3, 4, 0, 0), OrderStatus.CANCELLED, ["BOOK:10:50.00"], "0"),
    ("ORD-0005", datetime(2024, 3, 4, 8, 0), OrderStatus.DELIVERED, ["GIFT:1:2.26"], "0"),
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
    code, out, err = run(capsys, "--db", db, "products")
    assert code == 0, err
    # PEN: 6.00*0.9=5.40, 3.00, 2.00 -> 10.40 ; WIDGET: 1.125->1.13 twice -> 2.26 ; GIFT 2.26
    assert out == (
        HEADER
        + "PEN,4,2,10.40\n"
        + "GIFT,1,1,2.26\n"
        + "WIDGET,2,2,2.26\n"
    )


def test_inclusive_dates(db, capsys):
    code, out, _ = run(capsys, "--db", db, "products", "--from", "2024-03-02", "--to", "2024-03-03")
    assert code == 0
    assert out == HEADER + "PEN,2,1,5.00\n" + "WIDGET,1,1,1.13\n"


def test_cancelled_orders_ignored(db, capsys):
    code, out, _ = run(capsys, "--db", db, "products", "--from", "2024-03-04", "--to", "2024-03-04")
    assert code == 0
    assert out == HEADER + "GIFT,1,1,2.26\n"


def test_output_file(db, capsys, tmp_path):
    target = tmp_path / "products.csv"
    code, out, err = run(capsys, "--db", db, "products", "--output", str(target))
    assert code == 0, err
    assert out == ""
    assert err.strip() == f"exported 3 rows to {target}"
    with open(target, newline="", encoding="utf-8") as fh:
        assert fh.read().startswith(HEADER + "PEN,4,2,10.40\n")


def test_errors_are_clean(db, capsys, tmp_path):
    code, _, err = run(capsys, "--db", db, "products", "--output", str(tmp_path / "missing" / "p.csv"))
    assert code == 1 and err.startswith("error: ")
    code, _, err = run(capsys, "--db", db, "products", "--from", "2024-04-31")
    assert code == 1 and err.startswith("error: invalid date")


def test_uses_the_shared_csv_writer(db, capsys, monkeypatch):
    original = export_mod.write_csv
    calls = []

    def spy(header, rows, stream):
        calls.append(list(header))
        return original(header, rows, stream)

    for name, module in list(sys.modules.items()):
        if (name == "ordertool" or name.startswith("ordertool.")) and getattr(module, "write_csv", None) is original:
            monkeypatch.setattr(module, "write_csv", spy)
    code, _, _ = run(capsys, "--db", db, "products")
    assert code == 0
    assert calls == [["sku", "units", "orders", "revenue"]]
