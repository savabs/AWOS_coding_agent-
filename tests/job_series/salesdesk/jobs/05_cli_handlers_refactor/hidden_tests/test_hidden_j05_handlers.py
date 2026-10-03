"""Job 05: one cmd_<name>(service, args, out) handler per command, output unchanged."""

import inspect
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
from salesdesk.settings import SettingsStore, UnknownCustomer  # noqa: E402
from salesdesk.storage import Storage  # noqa: E402

UTC = timezone.utc

COMMANDS = {
    "add-customer": ["add-customer", "x", "X"],
    "set-timezone": ["set-timezone", "x", "UTC"],
    "import": ["import", "f.csv"],
    "report": ["report", "x"],
    "orders": ["orders", "x"],
    "breakdown": ["breakdown", "x", "2026-03-01", "2026-03-02"],
    "week": ["week", "x"],
}


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "s.db")
    with Storage(path) as storage:
        SettingsStore(storage).register("kiwi", "Kiwi Kites", "Pacific/Auckland", "NZD")
        storage.add_orders([
            Order("k1", "kiwi", 1500, datetime(2026, 3, 8, 19, 0, tzinfo=UTC)),
            Order("k2", "kiwi", 125000, datetime(2026, 3, 9, 10, 0, tzinfo=UTC)),
            Order("k3", "kiwi", 25, datetime(2026, 3, 9, 11, 0, tzinfo=UTC), status="refunded"),
        ])
    return path


def run(*argv):
    out = io.StringIO()
    code = cli.main(list(argv), out)
    return code, out.getvalue()


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_every_command_is_registered_with_its_handler(command):
    args = cli.build_parser().parse_args(COMMANDS[command])
    name = "cmd_" + command.replace("-", "_")
    assert args.handler is getattr(cli, name)
    params = list(inspect.signature(args.handler).parameters)
    assert len(params) == 3


def test_main_has_no_dispatch_chain():
    assert "args.command" not in inspect.getsource(cli.main)


def test_errors_are_handled_centrally(db, monkeypatch):
    def boom(service, args, out):
        raise ValueError("boom")

    monkeypatch.setattr(cli, "cmd_report", boom)
    assert run("--db", db, "report", "kiwi") == (2, "error: boom\n")

    def missing(service, args, out):
        raise UnknownCustomer("zz")

    monkeypatch.setattr(cli, "cmd_week", missing)
    assert run("--db", db, "week", "kiwi") == (2, "error: unknown customer zz\n")


def test_exit_status_is_what_the_handler_returns(db, monkeypatch):
    def handler(service, args, out):
        assert service.storage is not None and service.settings is not None and service.clock is not None
        out.write("hi\n")
        return 3

    monkeypatch.setattr(cli, "cmd_orders", handler)
    assert run("--db", db, "orders", "kiwi") == (3, "hi\n")


GOLDEN = [
    (["report", "kiwi", "--day", "2026-03-09"],
     "Daily sales — Kiwi Kites\nDay:       2026-03-09 (Pacific/Auckland)\nOrders:    2\n"
     "Total:     NZ$1,265.00\nAverage:   NZ$632.50\n"),
    (["--now", "2026-03-09T12:00:00Z", "report", "kiwi", "--yesterday"],
     "Daily sales — Kiwi Kites\nDay:       2026-03-09 (Pacific/Auckland)\nOrders:    2\n"
     "Total:     NZ$1,265.00\nAverage:   NZ$632.50\n"),
    (["orders", "kiwi", "--day", "2026-03-09"],
     "k1  2026-03-09 08:00  NZ$15.00  paid\nk2  2026-03-09 23:00  NZ$1,250.00  paid\n"),
    (["breakdown", "kiwi", "2026-03-08", "2026-03-10"],
     "day         orders        total\n2026-03-08       0      NZ$0.00\n2026-03-09       2  NZ$1,265.00\n"
     "2026-03-10       0      NZ$0.00\nTOTAL            2  NZ$1,265.00\n"),
    (["breakdown", "kiwi", "2026-03-08", "2026-03-10", "--csv"],
     "day,order_count,total_cents,refunded_cents\n2026-03-08,0,0,0\n2026-03-09,2,126500,0\n2026-03-10,0,0,25\n"),
    (["--now", "2026-03-10T01:00:00Z", "week", "kiwi"],
     "day         orders        total\n2026-03-09       2  NZ$1,265.00\n2026-03-10       0      NZ$0.00\n"
     "TOTAL            2  NZ$1,265.00\n"),
    (["set-timezone", "kiwi", "nzt"], "updated kiwi (Pacific/Auckland)\n"),
    (["add-customer", "zz", "Zed", "--timezone", "est", "--currency", "eur"], "registered zz (America/New_York)\n"),
    (["report", "nobody"], "error: unknown customer nobody\n"),
    (["breakdown", "kiwi", "2026-03-10", "2026-03-08"], "error: last day is before first day\n"),
]


@pytest.mark.parametrize("argv,expected", GOLDEN, ids=[" ".join(a) for a, _ in GOLDEN])
def test_output_unchanged(db, argv, expected):
    code, out = run("--db", db, *argv)
    assert out == expected
    assert code == (2 if expected.startswith("error: ") else 0)


def test_import_output_unchanged(db, tmp_path):
    csv_path = tmp_path / "in.csv"
    csv_path.write_text(
        "order_id,customer_id,amount,created_at\n"
        "n1,kiwi,10.00,2026-03-10T01:00:00Z\n"
        "n2,kiwi,oops,2026-03-10T02:00:00Z\n",
        encoding="utf-8",
    )
    assert run("--db", db, "import", str(csv_path)) == (
        0, "imported 1 orders\n  skipped line 3: invalid amount: 'oops'\n")
