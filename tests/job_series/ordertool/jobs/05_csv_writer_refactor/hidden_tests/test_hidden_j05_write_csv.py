"""Job 05: one shared `write_csv` behind every CSV the tool writes; output unchanged."""

import io
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


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "orders.csv")
    store = OrderStore(path)
    store.add(Order(id="ORD-0001", customer="Ada Lovelace", email="ada@example.com",
                    created_at=datetime(2024, 3, 1, 9, 15), status=OrderStatus.PAID,
                    items=[LineItem.from_spec("WIDGET:2:9.99")]))
    store.add(Order(id="ORD-0002", customer="Hopper, Grace", email="grace@example.com",
                    created_at=datetime(2024, 3, 5, 17, 40), status=OrderStatus.SHIPPED,
                    items=[LineItem.from_spec("GADGET:1:25.00"), LineItem.from_spec("CABLE:3:4.50")]))
    return path


EXPECTED_EXPORT = (
    "id,created_at,customer,email,status,items,total\n"
    "ORD-0001,2024-03-01T09:15:00,Ada Lovelace,ada@example.com,paid,2,19.98\n"
    "ORD-0002,2024-03-05T17:40:00,\"Hopper, Grace\",grace@example.com,shipped,4,38.50\n"
)
EXPECTED_LINES = (
    "order_id,sku,quantity,unit_price,subtotal\n"
    "ORD-0001,WIDGET,2,9.99,19.98\n"
    "ORD-0002,GADGET,1,25.00,25.00\n"
    "ORD-0002,CABLE,3,4.50,13.50\n"
)
EXPECTED_CUSTOMERS = (
    "customer,email,orders,total_spent\n"
    "\"Hopper, Grace\",grace@example.com,1,38.50\n"
    "Ada Lovelace,ada@example.com,1,19.98\n"
)


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_write_csv_contract():
    buf = io.StringIO()
    count = export_mod.write_csv(["a", "b"], [[1, "x,y"], ["z", ""]], buf)
    assert count == 2
    assert buf.getvalue() == 'a,b\n1,"x,y"\nz,\n'


def test_write_csv_accepts_a_generator_and_no_rows():
    buf = io.StringIO()
    assert export_mod.write_csv(["only"], (r for r in []), buf) == 0
    assert buf.getvalue() == "only\n"
    buf = io.StringIO()
    assert export_mod.write_csv(["n"], ([i] for i in range(3)), buf) == 3
    assert buf.getvalue() == "n\n0\n1\n2\n"


@pytest.mark.parametrize("command,expected", [
    (["export"], EXPECTED_EXPORT),
    (["export", "--line-items"], EXPECTED_LINES),
    (["customers"], EXPECTED_CUSTOMERS),
])
def test_output_unchanged(db, capsys, command, expected):
    code, out, err = run(capsys, "--db", db, *command)
    assert code == 0, err
    assert out == expected


@pytest.mark.parametrize("command,header", [
    (["export"], ["id", "created_at", "customer", "email", "status", "items", "total"]),
    (["export", "--line-items"], ["order_id", "sku", "quantity", "unit_price", "subtotal"]),
    (["customers"], ["customer", "email", "orders", "total_spent"]),
])
def test_every_csv_goes_through_write_csv(db, capsys, monkeypatch, tmp_path, command, header):
    original = export_mod.write_csv
    calls = []

    def spy(hdr, rows, stream):
        calls.append(list(hdr))
        return original(hdr, rows, stream)

    for name, module in list(sys.modules.items()):
        if (name == "ordertool" or name.startswith("ordertool.")) and getattr(module, "write_csv", None) is original:
            monkeypatch.setattr(module, "write_csv", spy)

    code, _, err = run(capsys, "--db", db, *command)
    assert code == 0, err
    target = tmp_path / "out.csv"
    code, _, err = run(capsys, "--db", db, *command, "--output", str(target))
    assert code == 0, err
    assert err.strip() == f"exported {2 if command != ['export', '--line-items'] else 3} rows to {target}"
    assert calls == [header, header]
