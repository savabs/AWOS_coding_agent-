"""CLI behaviour: output, errors and a non-UTC merchant end to end."""

import io
from datetime import datetime, timezone

from salesdesk.cli import main
from salesdesk.models import Order
from salesdesk.settings import SettingsStore
from salesdesk.storage import Storage
from salesdesk.timeutil import format_local

UTC = timezone.utc


def _db(tmp_path):
    path = str(tmp_path / "s.db")
    with Storage(path) as storage:
        SettingsStore(storage).register("berlin", "Berlin Bikes", "Europe/Berlin", "EUR")
        storage.add_orders([
            # 23:30 UTC on the 9th is already the 10th in Berlin
            Order("b1", "berlin", 1250, datetime(2026, 3, 9, 23, 30, tzinfo=UTC)),
            Order("b2", "berlin", 800, datetime(2026, 3, 10, 12, 0, tzinfo=UTC)),
            Order("b3", "berlin", 99, datetime(2026, 3, 10, 13, 0, tzinfo=UTC), status="refunded"),
        ])
    return path


def run(*argv):
    out = io.StringIO()
    code = main(list(argv), out)
    return code, out.getvalue()


def test_report_for_a_berlin_merchant(tmp_path):
    db = _db(tmp_path)
    code, out = run("--db", db, "report", "berlin", "--day", "2026-03-10")
    assert code == 0
    assert out == (
        "Daily sales — Berlin Bikes\n"
        "Day:       2026-03-10 (Europe/Berlin)\n"
        "Orders:    2\n"
        "Total:     €20.50\n"
        "Average:   €10.25\n"
        "Refunded:  €0.99\n"
    )


def test_breakdown_csv(tmp_path):
    db = _db(tmp_path)
    code, out = run("--db", db, "breakdown", "berlin", "2026-03-09", "2026-03-10", "--csv")
    assert code == 0
    assert out == "day,order_count,total_cents,refunded_cents\n2026-03-09,0,0,0\n2026-03-10,2,2050,99\n"


def test_errors_are_one_line_with_exit_status_2(tmp_path):
    db = _db(tmp_path)
    assert run("--db", db, "report", "nobody") == (2, "error: unknown customer nobody\n")
    code, out = run("--db", db, "breakdown", "berlin", "2026-03-10", "2026-03-09")
    assert (code, out) == (2, "error: last day is before first day\n")
    code, out = run("--db", db, "add-customer", "x", "X", "--timezone", "Mars/Olympus")
    assert code == 2 and out.startswith("error: unknown timezone")


def test_format_local():
    assert format_local(datetime(2026, 3, 9, 23, 30), "Europe/Berlin") == "2026-03-10 00:30"
