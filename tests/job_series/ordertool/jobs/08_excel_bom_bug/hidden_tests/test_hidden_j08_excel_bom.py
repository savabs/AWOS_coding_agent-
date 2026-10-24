"""Job 08: orders files saved by Excel (UTF-8 BOM, CRLF) load; writes stay BOM-free."""

import os
import sys
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from ordertool.cli import main  # noqa: E402
from ordertool.errors import StorageError  # noqa: E402
from ordertool.storage import OrderStore  # noqa: E402

BOM = "﻿"
CURRENT = (
    "id,customer,email,status,created_at,items,discount_pct\r\n"
    "ORD-0001,Ada Lovelace,ada@example.com,paid,2024-03-01T09:15:00,WIDGET:2:9.99,0\r\n"
    "ORD-0002,Grace Hopper,grace@example.com,pending,2024-03-05T17:40:00,GADGET:1:25.00|CABLE:3:4.50,10\r\n"
)
LEGACY = (
    "id,customer,email,status,created_at,items\r\n"
    "ORD-0001,Ada Lovelace,ada@example.com,paid,2024-03-01T09:15:00,WIDGET:2:9.99\r\n"
    "ORD-0002,\"Hopper, Grace\",grace@example.com,pending,2024-03-05T17:40:00,GADGET:1:25.00|CABLE:3:4.50\r\n"
)


def write(tmp_path, text):
    path = tmp_path / "orders.csv"
    path.write_bytes((BOM + text).encode("utf-8"))
    return str(path)


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.mark.parametrize("text", [CURRENT, LEGACY])
def test_excel_file_loads(tmp_path, text):
    orders = OrderStore(write(tmp_path, text)).load_all()
    assert [o.id for o in orders] == ["ORD-0001", "ORD-0002"]
    assert orders[0].total == Decimal("19.98")


def test_discount_column_read_from_excel_file(tmp_path):
    orders = OrderStore(write(tmp_path, CURRENT)).load_all()
    assert orders[1].total == Decimal("34.65")  # 38.50 less 10%


def test_cli_list_works(tmp_path, capsys):
    db = write(tmp_path, LEGACY)
    code, out, err = run(capsys, "--db", db, "list")
    assert code == 0, err
    assert out.splitlines()[0].startswith("ORD-0001  2024-03-01T09:15:00  paid")
    assert out.splitlines()[1].endswith("Hopper, Grace")


def test_next_write_drops_the_bom(tmp_path, capsys):
    db = write(tmp_path, CURRENT)
    code, _, err = run(capsys, "--db", db, "set-status", "ORD-0002", "paid")
    assert code == 0, err
    raw = open(db, "rb").read()
    assert not raw.startswith(BOM.encode("utf-8"))
    assert raw.startswith(b"id,customer,email,status,created_at,items")
    assert [o.status.value for o in OrderStore(db).load_all()] == ["paid", "paid"]


def test_add_to_excel_file_continues_numbering(tmp_path, capsys):
    db = write(tmp_path, LEGACY)
    code, out, err = run(capsys, "--db", db, "add", "--customer", "Alan", "--email", "alan@example.com",
                         "--item", "PEN:1:1.00", "--created-at", "2024-03-06T10:00:00")
    assert code == 0, err
    assert out.startswith("created ORD-0003")
    assert [o.id for o in OrderStore(db).load_all()] == ["ORD-0001", "ORD-0002", "ORD-0003"]


def test_wrong_columns_still_rejected(tmp_path, capsys):
    db = write(tmp_path, "order,name\r\nORD-0001,Ada\r\n")
    with pytest.raises(StorageError) as excinfo:
        OrderStore(db).load_all()
    assert "unexpected columns" in str(excinfo.value)
    code, _, err = run(capsys, "--db", db, "list")
    assert code == 1
    assert err.startswith("error: ")
    assert "unexpected columns" in err


def test_plain_files_unaffected(tmp_path):
    path = tmp_path / "plain.csv"
    path.write_text(LEGACY.replace("\r\n", "\n"), encoding="utf-8")
    assert len(OrderStore(str(path)).load_all()) == 2
