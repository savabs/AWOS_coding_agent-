"""Job 12: `export CUSTOMER FIRST LAST` CSV of single orders (local days and times, integer cents)."""

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
HEADER = "order_id,local_time,amount_cents,status\n"


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "s.db")
    with Storage(path) as storage:
        SettingsStore(storage).register("la", "LA Lamps", "America/Los_Angeles", "USD")
        storage.add_orders([
            # US DST starts 2026-03-08 at 02:00 local (PST -8 -> PDT -7)
            Order("l3", "la", 123456, datetime(2026, 3, 8, 10, 30, tzinfo=UTC), status="cancelled"),
            Order("l0", "la", 1, datetime(2026, 3, 7, 7, 59, tzinfo=UTC)),       # 6th 23:59 local
            Order("l1", "la", 1000, datetime(2026, 3, 7, 8, 0, tzinfo=UTC)),     # 7th 00:00
            Order("l2", "la", 250, datetime(2026, 3, 8, 9, 30, tzinfo=UTC), status="refunded"),  # 01:30 PST
            Order("l4", "la", 5, datetime(2026, 3, 9, 6, 59, tzinfo=UTC)),       # 8th 23:59 PDT
            Order("l5", "la", 7, datetime(2026, 3, 9, 7, 0, tzinfo=UTC)),        # 9th 00:00
        ])
    return path


def run(*argv):
    out = io.StringIO()
    code = cli.main(list(argv), out)
    return code, out.getvalue()


def test_export_over_the_dst_change(db):
    assert run("--db", db, "export", "la", "2026-03-07", "2026-03-08") == (0, (
        HEADER
        + "l1,2026-03-07 00:00,1000,paid\n"
        + "l2,2026-03-08 01:30,250,refunded\n"
        + "l3,2026-03-08 03:30,123456,cancelled\n"
        + "l4,2026-03-08 23:59,5,paid\n"
    ))


def test_single_day_and_empty_range(db):
    assert run("--db", db, "export", "la", "2026-03-09", "2026-03-09") == (0, HEADER + "l5,2026-03-09 00:00,7,paid\n")
    assert run("--db", db, "export", "la", "2026-04-01", "2026-04-30") == (0, HEADER)


def test_same_days_as_breakdown(db):
    code, out = run("--db", db, "breakdown", "la", "2026-03-07", "2026-03-08", "--csv")
    assert code == 0
    assert out.splitlines()[1:] == ["2026-03-07,1,1000,0", "2026-03-08,1,5,250"]


def test_errors_are_the_usual_ones(db):
    assert run("--db", db, "export", "la", "2026-03-08", "2026-03-07") == (
        2, "error: last day is before first day\n")
    assert run("--db", db, "export", "nobody", "2026-03-07", "2026-03-08") == (
        2, "error: unknown customer nobody\n")
    code, out = run("--db", db, "export", "la", "2026-03-07", "2026-03-32")
    assert code == 2 and out.startswith("error: ") and out.count("\n") == 1


def test_registered_like_every_other_command():
    args = cli.build_parser().parse_args(["export", "la", "2026-03-07", "2026-03-08"])
    assert args.handler is cli.cmd_export
