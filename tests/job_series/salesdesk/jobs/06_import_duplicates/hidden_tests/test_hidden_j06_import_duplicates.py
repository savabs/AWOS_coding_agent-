"""Job 06: import skips duplicate ids / unknown customers like other bad rows (no traceback)."""

import io
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from salesdesk.cli import main  # noqa: E402
from salesdesk.importer import import_orders  # noqa: E402
from salesdesk.models import Order  # noqa: E402
from salesdesk.settings import SettingsStore  # noqa: E402
from salesdesk.storage import Storage  # noqa: E402

UTC = timezone.utc

CSV = (
    "order_id,customer_id,amount,created_at\n"
    "o1,acme,50.00,2026-03-10T08:00:00Z\n"      # line 2: already in the database
    "o2,acme,10.00,2026-03-10T09:00:00Z\n"      # line 3: ok
    "o3,zz,5.00,2026-03-10T10:00:00Z\n"         # line 4: customer not registered
    "o2,acme,99.00,2026-03-10T11:00:00Z\n"      # line 5: repeated in this file
    "o4,acme,oops,2026-03-10T12:00:00Z\n"       # line 6: bad amount
    "o5,acme,1.00,2026-03-10T13:00:00Z\n"       # line 7: ok
)

EXPECTED = (
    "imported 2 orders\n"
    "  skipped line 2: duplicate order_id 'o1'\n"
    "  skipped line 4: unknown customer 'zz'\n"
    "  skipped line 5: duplicate order_id 'o2'\n"
    "  skipped line 6: invalid amount: 'oops'\n"
)


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "s.db")
    with Storage(path) as storage:
        SettingsStore(storage).register("acme", "Acme")
        storage.add_order(Order("o1", "acme", 1000, datetime(2026, 3, 1, 8, 0, tzinfo=UTC)))
    return path


@pytest.fixture
def csv_file(tmp_path):
    p = tmp_path / "orders.csv"
    p.write_text(CSV, encoding="utf-8")
    return str(p)


def run(*argv):
    out = io.StringIO()
    code = main(list(argv), out)
    return code, out.getvalue()


def amounts(db):
    with Storage(db) as storage:
        return {o.order_id: o.amount_cents for o in storage.all_orders("acme")}


def test_bad_rows_are_skipped_and_the_rest_imported(db, csv_file):
    assert run("--db", db, "import", csv_file) == (0, EXPECTED)
    assert amounts(db) == {"o1": 1000, "o2": 1000, "o5": 100}


def test_importing_the_same_file_twice(db, csv_file):
    assert run("--db", db, "import", csv_file)[0] == 0
    code, out = run("--db", db, "import", csv_file)
    assert code == 0
    assert out.splitlines()[0] == "imported 0 orders"
    assert "  skipped line 3: duplicate order_id 'o2'" in out.splitlines()
    assert "  skipped line 7: duplicate order_id 'o5'" in out.splitlines()
    assert amounts(db) == {"o1": 1000, "o2": 1000, "o5": 100}


def test_import_orders_api(db):
    with Storage(db) as storage:
        result = import_orders(storage, io.StringIO(CSV))
        assert result.imported == 2
        assert result.errors == [
            "line 2: duplicate order_id 'o1'",
            "line 4: unknown customer 'zz'",
            "line 5: duplicate order_id 'o2'",
            "line 6: invalid amount: 'oops'",
        ]
        assert [o.order_id for o in storage.all_orders("zz")] == []
