"""Job 01: `list --from/--to` (inclusive whole days, clean errors)."""

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
    ("ORD-0001", "Ada Lovelace", datetime(2024, 3, 9, 23, 59, 59), OrderStatus.PENDING, "WIDGET:1:10.00"),
    ("ORD-0002", "Grace Hopper", datetime(2024, 3, 10, 0, 0, 0), OrderStatus.PAID, "GADGET:2:25.00"),
    ("ORD-0003", "Alan Turing", datetime(2024, 3, 15, 12, 30, 0), OrderStatus.SHIPPED, "CABLE:3:4.50"),
    ("ORD-0004", "Ada Byron", datetime(2024, 3, 20, 23, 59, 59), OrderStatus.SHIPPED, "WIDGET:4:10.00"),
    ("ORD-0005", "Barbara Liskov", datetime(2024, 3, 21, 0, 0, 0), OrderStatus.CANCELLED, "GADGET:1:25.00"),
]


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "orders.csv")
    store = OrderStore(path)
    for order_id, customer, created, status, item in SEED:
        store.add(Order(id=order_id, customer=customer, email=f"{order_id.lower()}@example.com",
                        created_at=created, status=status, items=[LineItem.from_spec(item)]))
    return path


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def listed_ids(out):
    return [line.split()[0] for line in out.splitlines() if line.startswith("ORD-")]


def test_to_includes_the_whole_last_day(db, capsys):
    code, out, err = run(capsys, "--db", db, "list", "--to", "2024-03-20")
    assert code == 0, err
    assert listed_ids(out) == ["ORD-0001", "ORD-0002", "ORD-0003", "ORD-0004"]


def test_from_starts_at_midnight(db, capsys):
    code, out, _ = run(capsys, "--db", db, "list", "--from", "2024-03-10")
    assert code == 0
    assert listed_ids(out) == ["ORD-0002", "ORD-0003", "ORD-0004", "ORD-0005"]


def test_single_day(db, capsys):
    code, out, _ = run(capsys, "--db", db, "list", "--from", "2024-03-09", "--to", "2024-03-09")
    assert code == 0
    assert listed_ids(out) == ["ORD-0001"]


def test_combines_with_status_and_customer(db, capsys):
    code, out, _ = run(capsys, "--db", db, "list", "--from", "2024-03-10", "--to", "2024-03-31",
                       "--status", "shipped", "--customer", "ada")
    assert code == 0
    assert listed_ids(out) == ["ORD-0004"]


def test_line_format_unchanged(db, capsys):
    code, out, _ = run(capsys, "--db", db, "list", "--from", "2024-03-15", "--to", "2024-03-15")
    assert code == 0
    assert out == "ORD-0003  2024-03-15T12:30:00  shipped        13.50  Alan Turing\n"


def test_no_matches(db, capsys):
    code, out, _ = run(capsys, "--db", db, "list", "--from", "2025-01-01")
    assert code == 0
    assert out.strip() == "no orders"


@pytest.mark.parametrize("flag,value", [("--from", "2024-02-30"), ("--to", "10/03/2024")])
def test_bad_date_is_a_clean_error(db, capsys, flag, value):
    code, out, err = run(capsys, "--db", db, "list", flag, value)
    assert code == 1
    assert err.startswith("error: invalid date")
    assert value in err
    assert "ORD-" not in out


def test_backwards_range_is_rejected_like_export(db, capsys):
    code, out, err = run(capsys, "--db", db, "list", "--from", "2024-03-20", "--to", "2024-03-10")
    assert code == 1
    assert err.startswith("error: ")
    assert "ORD-" not in out
