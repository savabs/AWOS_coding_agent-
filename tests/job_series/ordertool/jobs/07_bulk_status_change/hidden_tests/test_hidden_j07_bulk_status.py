"""Job 07: `set-status ID [ID ...] STATUS`, all or nothing, same errors as before."""

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

STATUSES = [OrderStatus.PENDING, OrderStatus.PAID, OrderStatus.PAID, OrderStatus.DELIVERED, OrderStatus.PAID]


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "orders.csv")
    store = OrderStore(path)
    for n, status in enumerate(STATUSES, start=1):
        store.add(Order(id=f"ORD-{n:04d}", customer=f"C{n}", email=f"c{n}@example.com",
                        created_at=datetime(2024, 3, n, 12, 0), status=status,
                        items=[LineItem.from_spec("WIDGET:1:1.00")], discount_pct="10"))
    return path


def statuses(db):
    return [o.status.value for o in OrderStore(db).load_all()]


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_several_ids(db, capsys):
    code, out, err = run(capsys, "--db", db, "set-status", "ORD-0005", "ORD-0002", "ORD-0003", "shipped")
    assert code == 0, err
    assert out == "ORD-0005 is now shipped\nORD-0002 is now shipped\nORD-0003 is now shipped\n"
    assert statuses(db) == ["pending", "shipped", "shipped", "delivered", "shipped"]


def test_status_name_is_case_insensitive(db, capsys):
    code, out, err = run(capsys, "--db", db, "set-status", "ORD-0002", "ORD-0003", "Shipped")
    assert code == 0, err
    assert statuses(db)[1:3] == ["shipped", "shipped"]


def test_invalid_transition_changes_nothing(db, capsys):
    code, out, err = run(capsys, "--db", db, "set-status", "ORD-0002", "ORD-0004", "ORD-0003", "shipped")
    assert code == 1
    assert err.startswith("error: cannot change ORD-0004 from delivered to shipped")
    assert "is now" not in out
    assert statuses(db) == ["pending", "paid", "paid", "delivered", "paid"]


def test_unknown_id_changes_nothing(db, capsys):
    code, out, err = run(capsys, "--db", db, "set-status", "ORD-0002", "ORD-0099", "shipped")
    assert code == 1
    assert err.startswith("error: no order with id")
    assert "ORD-0099" in err
    assert statuses(db) == ["pending", "paid", "paid", "delivered", "paid"]


def test_unknown_status_is_clean_error(db, capsys):
    code, _, err = run(capsys, "--db", db, "set-status", "ORD-0001", "ORD-0002", "lost")
    assert code == 1
    assert err.startswith("error: unknown status")
    assert statuses(db)[0:2] == ["pending", "paid"]


def test_already_in_status_is_fine(db, capsys):
    code, out, err = run(capsys, "--db", db, "set-status", "ORD-0001", "ORD-0002", "paid")
    assert code == 0, err
    assert out == "ORD-0001 is now paid\nORD-0002 is now paid\n"
    assert statuses(db)[0:2] == ["paid", "paid"]


def test_single_id_unchanged(db, capsys):
    code, out, _ = run(capsys, "--db", db, "set-status", "ORD-0001", "paid")
    assert code == 0
    assert out == "ORD-0001 is now paid\n"
    code, _, err = run(capsys, "--db", db, "set-status", "ORD-0004", "pending")
    assert code == 1
    assert err.startswith("error: cannot change ORD-0004 from delivered to pending")


def test_other_fields_are_preserved(db, capsys):
    before = {o.id: (o.customer, o.created_at, str(o.total)) for o in OrderStore(db).load_all()}
    assert run(capsys, "--db", db, "set-status", "ORD-0002", "ORD-0003", "cancelled")[0] == 0
    after = {o.id: (o.customer, o.created_at, str(o.total)) for o in OrderStore(db).load_all()}
    assert before == after
    assert after["ORD-0002"][2] == "0.90"
