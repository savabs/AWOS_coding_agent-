"""Job 02: `set-timezone CUSTOMER TZ` (same validation/aliases as add-customer, usual errors)."""

import io
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from salesdesk.cli import main  # noqa: E402
from salesdesk.models import Order  # noqa: E402
from salesdesk.settings import SettingsStore  # noqa: E402
from salesdesk.storage import Storage  # noqa: E402

UTC = timezone.utc


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "s.db")
    with Storage(path) as storage:
        SettingsStore(storage).register("shop", "Corner Shop", "UTC", "GBP")
        storage.add_orders([
            Order("s1", "shop", 1000, datetime(2026, 3, 10, 5, 0, tzinfo=UTC)),   # 22:00 on the 9th in LA
            Order("s2", "shop", 2000, datetime(2026, 3, 10, 12, 0, tzinfo=UTC)),
        ])
    return path


def run(*argv):
    out = io.StringIO()
    code = main(list(argv), out)
    return code, out.getvalue()


def customer(db, customer_id):
    with Storage(db) as storage:
        return storage.get_customer(customer_id)


def test_sets_an_iana_zone(db):
    assert run("--db", db, "set-timezone", "shop", "Europe/Berlin") == (0, "updated shop (Europe/Berlin)\n")
    c = customer(db, "shop")
    assert (c.name, c.timezone, c.currency) == ("Corner Shop", "Europe/Berlin", "GBP")


@pytest.mark.parametrize("given,stored", [
    ("pst", "America/Los_Angeles"),
    ("IST", "Asia/Kolkata"),
    ("  Pacific/Auckland ", "Pacific/Auckland"),
    ("GMT", "UTC"),
])
def test_accepts_the_same_names_as_add_customer(db, given, stored):
    assert run("--db", db, "set-timezone", "shop", given) == (0, f"updated shop ({stored})\n")
    assert customer(db, "shop").timezone == stored


def test_reports_follow_the_new_zone(db):
    assert run("--db", db, "set-timezone", "shop", "PST")[0] == 0
    code, out = run("--db", db, "report", "shop", "--day", "2026-03-09")
    assert code == 0, out
    assert "Day:       2026-03-09 (America/Los_Angeles)" in out
    assert "Total:     £10.00" in out


def test_invalid_zone_is_the_usual_error_and_changes_nothing(db):
    code, out = run("--db", db, "set-timezone", "shop", "Mars/Olympus")
    assert code == 2
    assert out == "error: unknown timezone: 'Mars/Olympus'\n"
    assert customer(db, "shop").timezone == "UTC"


def test_unknown_customer_is_the_usual_error(db):
    assert run("--db", db, "set-timezone", "ghost", "UTC") == (2, "error: unknown customer ghost\n")
    assert customer(db, "ghost") is None
