"""Job 02: `customers` CSV report (export conventions: CSV format, --output, money, dates)."""

import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from ordertool.cli import main  # noqa: E402
from ordertool.models import LineItem, Order, OrderStatus  # noqa: E402
from ordertool.storage import OrderStore  # noqa: E402

SEED = [
    # id, name, email, created, status, items
    ("ORD-0001", "Ada Lovelace", "ada@example.com", datetime(2024, 3, 1, 9, 0), OrderStatus.PAID, ["WIDGET:2:9.99"]),
    ("ORD-0002", "Grace Hopper", "grace@example.com", datetime(2024, 3, 5, 17, 40), OrderStatus.SHIPPED,
     ["GADGET:1:25.00", "CABLE:3:4.50"]),
    ("ORD-0003", "Ada King", "Ada@Example.com", datetime(2024, 3, 9, 23, 59, 59), OrderStatus.PENDING, ["BOOK:1:18.52"]),
    ("ORD-0004", "Alan Turing", "alan@example.com", datetime(2024, 3, 10, 8, 0), OrderStatus.CANCELLED, ["BOOK:1:79.90"]),
    ("ORD-0005", "Grace Hopper", "grace@example.com", datetime(2024, 3, 12, 10, 0), OrderStatus.CANCELLED, ["GADGET:9:25.00"]),
    ("ORD-0006", "Hopper, Grace", "GRACE@example.com", datetime(2024, 3, 2, 10, 0), OrderStatus.PAID, ["CABLE:1:0.01"]),
    ("ORD-0007", "Edsger Dijkstra", "edsger@example.com", datetime(2024, 3, 20, 12, 0), OrderStatus.DELIVERED, ["PEN:3:12.83"]),
]

@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "orders.csv")
    store = OrderStore(path)
    for order_id, name, email, created, status, items in SEED:
        store.add(Order(id=order_id, customer=name, email=email, created_at=created, status=status,
                        items=[LineItem.from_spec(i) for i in items]))
    return path


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_full_output_is_exact(db, capsys):
    code, out, err = run(capsys, "--db", db, "customers")
    assert code == 0, err
    # sorted by total desc: Grace 38.51, Ada 38.50, Edsger 38.49
    assert out == (
        "customer,email,orders,total_spent\n"
        "Grace Hopper,grace@example.com,2,38.51\n"
        "Ada King,ada@example.com,2,38.50\n"
        "Edsger Dijkstra,edsger@example.com,1,38.49\n"
    )


def test_output_file_and_message(db, capsys, tmp_path):
    target = tmp_path / "customers.csv"
    code, out, err = run(capsys, "--db", db, "customers", "--output", str(target))
    assert code == 0, err
    assert out == ""
    assert err.strip() == f"exported 3 rows to {target}"
    with open(target, newline="", encoding="utf-8") as fh:
        text = fh.read()
    assert text.startswith("customer,email,orders,total_spent\n")
    assert "\r" not in text
    assert text.count("\n") == 4


def test_name_with_comma_is_quoted(db, capsys):
    code, out, _ = run(capsys, "--db", db, "customers", "--to", "2024-03-02")
    assert code == 0
    assert out == (
        "customer,email,orders,total_spent\n"
        "Ada Lovelace,ada@example.com,1,19.98\n"
        "\"Hopper, Grace\",grace@example.com,1,0.01\n"
    )


def test_date_range_is_inclusive(db, capsys):
    code, out, _ = run(capsys, "--db", db, "customers", "--from", "2024-03-05", "--to", "2024-03-09")
    assert code == 0
    assert out == (
        "customer,email,orders,total_spent\n"
        "Grace Hopper,grace@example.com,1,38.50\n"
        "Ada King,ada@example.com,1,18.52\n"
    )


def test_only_cancelled_customer_left_out(db, capsys):
    code, out, _ = run(capsys, "--db", db, "customers")
    assert code == 0
    assert "alan@example.com" not in out


def test_bad_date_is_clean_error(db, capsys):
    code, out, err = run(capsys, "--db", db, "customers", "--from", "2024-13-01")
    assert code == 1
    assert err.startswith("error: invalid date")
    assert out == ""


def test_empty_database(tmp_path, capsys):
    code, out, _ = run(capsys, "--db", str(tmp_path / "none.csv"), "customers")
    assert code == 0
    assert out == "customer,email,orders,total_spent\n"
