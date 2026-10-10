"""Job 09: `import FILE` (same validation as add, all or nothing, line numbers, Excel files)."""

import os
import sys
from datetime import datetime
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from ordertool.cli import main  # noqa: E402
from ordertool.models import LineItem, Order, OrderStatus  # noqa: E402
from ordertool.storage import OrderStore  # noqa: E402

HEADER = "customer,email,created_at,items\n"


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "orders.csv")
    store = OrderStore(path)
    for n in (1, 2):
        store.add(Order(id=f"ORD-{n:04d}", customer=f"Old {n}", email=f"old{n}@example.com",
                        created_at=datetime(2024, 3, n, 12, 0), status=OrderStatus.PAID,
                        items=[LineItem.from_spec("WIDGET:1:1.00")]))
    return path


def write(tmp_path, text, name="shop.csv", bom=False):
    path = tmp_path / name
    path.write_bytes((("﻿" if bom else "") + text).encode("utf-8"))
    return str(path)


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def ids(db):
    return [o.id for o in OrderStore(db).load_all()]


def test_basic_import(db, tmp_path, capsys):
    src = write(tmp_path, HEADER
                + "Ada Lovelace,ada@example.com,2024-03-10T09:00:00,WIDGET:2:9.99\n"
                + "\"Hopper, Grace\",grace@example.com,2024-03-11T10:30:00,GADGET:1:25.00|cable:3:4.50\n")
    code, out, err = run(capsys, "--db", db, "import", src)
    assert code == 0, err
    assert out.strip() == "imported 2 orders"
    orders = OrderStore(db).load_all()
    assert [o.id for o in orders] == ["ORD-0001", "ORD-0002", "ORD-0003", "ORD-0004"]
    new = orders[2:]
    assert [o.status for o in new] == [OrderStatus.PENDING, OrderStatus.PENDING]
    assert new[1].customer == "Hopper, Grace"
    assert new[1].created_at == datetime(2024, 3, 11, 10, 30)
    assert [i.sku for i in new[1].items] == ["GADGET", "CABLE"]
    assert [o.total for o in new] == [Decimal("19.98"), Decimal("38.50")]


def test_optional_discount_column(db, tmp_path, capsys):
    src = write(tmp_path, "customer,email,created_at,items,discount\n"
                + "Ada,ada@example.com,2024-03-10T09:00:00,WIDGET:1:1.25,10\n"
                + "Bob,bob@example.com,2024-03-10T09:05:00,WIDGET:1:1.25,\n")
    code, _, err = run(capsys, "--db", db, "import", src)
    assert code == 0, err
    orders = OrderStore(db).load_all()
    assert [o.total for o in orders[2:]] == [Decimal("1.13"), Decimal("1.25")]


def test_excel_file(db, tmp_path, capsys):
    src = write(tmp_path, (HEADER + "Ada,ada@example.com,2024-03-10T09:00:00,WIDGET:1:2.00\n").replace("\n", "\r\n"),
                bom=True)
    code, out, err = run(capsys, "--db", db, "import", src)
    assert code == 0, err
    assert out.strip() == "imported 1 orders"
    assert OrderStore(db).get("ORD-0003").customer == "Ada"


def test_same_checks_as_add(db, tmp_path, capsys):
    src = write(tmp_path, HEADER
                + "  Ada  , ada@example.com ,2024-03-10T09:00:00,WIDGET:1:2.00\n")
    assert run(capsys, "--db", db, "import", src)[0] == 0
    order = OrderStore(db).get("ORD-0003")
    assert (order.customer, order.email) == ("Ada", "ada@example.com")


@pytest.mark.parametrize("bad_row,line", [
    ("Bob,not-an-email,2024-03-10T09:00:00,WIDGET:1:1.00", 3),
    ("Bob,bob@example.com,2024-03-32T09:00:00,WIDGET:1:1.00", 3),
    ("Bob,bob@example.com,2024-03-10T09:00:00,WIDGET:0:1.00", 3),
    (",bob@example.com,2024-03-10T09:00:00,WIDGET:1:1.00", 3),
    ("Bob,bob@example.com,2024-03-10T09:00:00,", 3),
])
def test_bad_row_imports_nothing(db, tmp_path, capsys, bad_row, line):
    src = write(tmp_path, HEADER
                + "Ada,ada@example.com,2024-03-10T09:00:00,WIDGET:1:1.00\n"
                + bad_row + "\n"
                + "Cy,cy@example.com,2024-03-10T09:00:00,WIDGET:1:1.00\n")
    code, out, err = run(capsys, "--db", db, "import", src)
    assert code == 1
    assert err.startswith("error: ")
    assert f"line {line}" in err
    assert "imported" not in out
    assert ids(db) == ["ORD-0001", "ORD-0002"]


def test_bad_discount_reports_line(db, tmp_path, capsys):
    src = write(tmp_path, "customer,email,created_at,items,discount\n"
                + "Ada,ada@example.com,2024-03-10T09:00:00,WIDGET:1:1.25,120\n")
    code, _, err = run(capsys, "--db", db, "import", src)
    assert code == 1
    assert err.startswith("error: ") and "line 2" in err
    assert ids(db) == ["ORD-0001", "ORD-0002"]


def test_missing_column(db, tmp_path, capsys):
    src = write(tmp_path, "customer,email,items\nAda,ada@example.com,WIDGET:1:1.00\n")
    code, _, err = run(capsys, "--db", db, "import", src)
    assert code == 1
    assert err.startswith("error: ")
    assert "created_at" in err
    assert ids(db) == ["ORD-0001", "ORD-0002"]


def test_missing_file(db, tmp_path, capsys):
    missing = str(tmp_path / "nope.csv")
    code, _, err = run(capsys, "--db", db, "import", missing)
    assert code == 1
    assert err.startswith("error: ")
    assert missing in err


def test_import_into_new_database(tmp_path, capsys):
    db = str(tmp_path / "fresh.csv")
    src = write(tmp_path, HEADER + "Ada,ada@example.com,2024-03-10T09:00:00,WIDGET:1:1.00\n")
    code, out, err = run(capsys, "--db", db, "import", src)
    assert code == 0, err
    assert ids(db) == ["ORD-0001"]
