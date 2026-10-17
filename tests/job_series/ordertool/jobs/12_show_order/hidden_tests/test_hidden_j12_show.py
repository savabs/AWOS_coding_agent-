"""Job 12: `show ORDER_ID` (money helper, discount consistent with total, NotFound as usual)."""

import os
import subprocess
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
    ("ORD-0001", "Ada Lovelace", "ada@example.com", datetime(2024, 3, 1, 9, 15), OrderStatus.PAID,
     ["WIDGET:2:9.99"], "0"),
    ("ORD-0002", "Grace Hopper", "grace@example.com", datetime(2024, 3, 5, 17, 40), OrderStatus.PENDING,
     ["GADGET:1:25.00", "CABLE:3:4.50"], "10"),
    ("ORD-0003", "Alan Turing", "alan@example.com", datetime(2024, 3, 9, 12, 0), OrderStatus.SHIPPED,
     ["WIDGET:1:1.25"], "10"),
    ("ORD-0004", "Edsger Dijkstra", "edsger@example.com", datetime(2024, 3, 10, 8, 30, 5), OrderStatus.CANCELLED,
     ["PEN:3:9.99"], "12.5"),
]


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "orders.csv")
    store = OrderStore(path)
    for order_id, name, email, created, status, items, discount in SEED:
        store.add(Order(id=order_id, customer=name, email=email, created_at=created, status=status,
                        items=[LineItem.from_spec(i) for i in items], discount_pct=discount))
    return path


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_example_from_the_request(db, capsys):
    code, out, err = run(capsys, "--db", db, "show", "ORD-0002")
    assert code == 0, err
    assert out == (
        "ORD-0002  Grace Hopper <grace@example.com>\n"
        "status: pending\n"
        "created: 2024-03-05T17:40:00\n"
        "GADGET x1 @ 25.00 = 25.00\n"
        "CABLE x3 @ 4.50 = 13.50\n"
        "subtotal: 38.50\n"
        "discount: 10% -3.85\n"
        "total: 34.65\n"
    )


def test_no_discount_line_without_discount(db, capsys):
    code, out, _ = run(capsys, "--db", db, "show", "ORD-0001")
    assert code == 0
    assert out == (
        "ORD-0001  Ada Lovelace <ada@example.com>\n"
        "status: paid\n"
        "created: 2024-03-01T09:15:00\n"
        "WIDGET x2 @ 9.99 = 19.98\n"
        "subtotal: 19.98\n"
        "total: 19.98\n"
    )


def test_discount_amount_matches_rounded_total(db, capsys):
    # 1.25 at 10% off: total 1.125 -> 1.13 (half up), so the discount shown is 0.12
    code, out, _ = run(capsys, "--db", db, "show", "ORD-0003")
    assert code == 0
    lines = out.splitlines()
    assert lines[-3:] == ["subtotal: 1.25", "discount: 10% -0.12", "total: 1.13"]


def test_fractional_percentage(db, capsys):
    # 29.97 at 12.5% off = 26.22375 -> 26.22 ; discount 3.75
    code, out, _ = run(capsys, "--db", db, "show", "ORD-0004")
    assert code == 0
    lines = out.splitlines()
    assert lines[1] == "status: cancelled"
    assert lines[2] == "created: 2024-03-10T08:30:05"
    assert lines[-3:] == ["subtotal: 29.97", "discount: 12.5% -3.75", "total: 26.22"]


def test_unknown_order_is_the_usual_error(db, capsys):
    code, out, err = run(capsys, "--db", db, "show", "ORD-0099")
    assert code == 1
    assert out == ""
    assert err == "error: no order with id 'ORD-0099'\n"


def test_real_cli(db):
    env = dict(os.environ)
    env.pop("ORDERTOOL_DB", None)
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run([sys.executable, "-m", "ordertool", "--db", db, "show", "ORD-0002"],
                            cwd=ROOT, capture_output=True, text=True, env=env, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith("total: 34.65\n")
