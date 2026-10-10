"""Job 08: `monthly CUSTOMER YEAR [--csv]` in the merchant's calendar, averages rounded half up."""

import io
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from salesdesk import cli  # noqa: E402
from salesdesk.models import Order  # noqa: E402
from salesdesk.settings import SettingsStore  # noqa: E402
from salesdesk.storage import Storage  # noqa: E402

UTC = timezone.utc
HEADER = "month,order_count,total_cents,refunded_cents,average_cents\n"


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "s.db")
    with Storage(path) as storage:
        settings = SettingsStore(storage)
        settings.register("kiwi", "Kiwi Kites", "Pacific/Auckland", "NZD")
        settings.register("la", "LA Lamps", "America/Los_Angeles", "USD")
        storage.add_orders([
            # Auckland is UTC+13 in summer (Dec-Mar)
            Order("k1", "kiwi", 1000, datetime(2026, 1, 31, 12, 0, tzinfo=UTC)),    # 1 Feb 01:00 local
            Order("k2", "kiwi", 1500, datetime(2026, 1, 15, 0, 0, tzinfo=UTC)),
            Order("k3", "kiwi", 1005, datetime(2026, 2, 10, 3, 0, tzinfo=UTC)),
            Order("k4", "kiwi", 500, datetime(2025, 12, 31, 11, 30, tzinfo=UTC)),   # 1 Jan 2026 00:30 local
            Order("k5", "kiwi", 999, datetime(2026, 12, 31, 11, 30, tzinfo=UTC)),   # 1 Jan 2027 local
            Order("k6", "kiwi", 300, datetime(2026, 3, 5, 3, 0, tzinfo=UTC), status="refunded"),
            Order("k7", "kiwi", 400, datetime(2026, 3, 6, 3, 0, tzinfo=UTC), status="cancelled"),
            # LA is UTC-8 in winter
            Order("l1", "la", 2000, datetime(2026, 1, 1, 5, 0, tzinfo=UTC)),       # 31 Dec 2025 local
            Order("l2", "la", 3000, datetime(2026, 2, 1, 7, 30, tzinfo=UTC)),      # 31 Jan 2026 local
        ])
    return path


def run(*argv):
    out = io.StringIO()
    code = cli.main(list(argv), out)
    return code, out.getvalue()


EMPTY = [f"2026-{m:02d},0,0,0,0\n" for m in range(4, 13)]


def test_csv_uses_the_merchants_months_and_rounds_averages_half_up(db):
    code, out = run("--db", db, "monthly", "kiwi", "2026", "--csv")
    assert code == 0, out
    assert out == (
        HEADER
        + "2026-01,2,2000,0,1000\n"
        + "2026-02,2,2005,0,1003\n"     # 2005 / 2 = 1002.5 -> 1003
        + "2026-03,0,0,300,0\n"
        + "".join(EMPTY)
    )


def test_text_table(db):
    code, out = run("--db", db, "monthly", "kiwi", "2026")
    assert code == 0, out
    rows = [ln.split() for ln in out.splitlines()]
    assert rows[0] == ["month", "orders", "total", "average"]
    assert rows[1] == ["2026-01", "2", "NZ$20.00", "NZ$10.00"]
    assert rows[2] == ["2026-02", "2", "NZ$20.05", "NZ$10.03"]
    assert rows[3] == ["2026-03", "0", "NZ$0.00", "NZ$0.00"]
    assert len(rows) == 14
    assert rows[-1] == ["TOTAL", "4", "NZ$40.05", "NZ$10.01"]


def test_los_angeles_year_boundary(db):
    code, out = run("--db", db, "monthly", "la", "2026", "--csv")
    assert code == 0
    assert out.splitlines()[1] == "2026-01,1,3000,0,3000"
    assert out.count("\n") == 13
    code, out = run("--db", db, "monthly", "la", "2025", "--csv")
    assert out.splitlines()[12] == "2025-12,1,2000,0,2000"


def test_unknown_customer(db):
    assert run("--db", db, "monthly", "nobody", "2026") == (2, "error: unknown customer nobody\n")


def test_registered_like_every_other_command():
    args = cli.build_parser().parse_args(["monthly", "kiwi", "2026"])
    assert args.handler is cli.cmd_monthly
