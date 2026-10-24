"""Behavioural tests for `ordertool export --from/--to/--status`."""

import csv
import io
import os
import subprocess
import sys
from datetime import datetime

import pytest

from ordertool.models import LineItem, Order, OrderStatus
from ordertool.storage import OrderStore

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SEED = [
    ("ORD-0001", "Ada Lovelace", datetime(2024, 3, 9, 23, 59, 59), OrderStatus.PENDING, "WIDGET:1:10.00"),
    ("ORD-0002", "Grace Hopper", datetime(2024, 3, 10, 0, 0, 0), OrderStatus.PAID, "GADGET:2:25.00"),
    ("ORD-0003", "Alan Turing", datetime(2024, 3, 15, 12, 30, 0), OrderStatus.SHIPPED, "CABLE:3:4.50"),
    ("ORD-0004", "Edsger Dijkstra", datetime(2024, 3, 20, 23, 59, 59), OrderStatus.SHIPPED, "WIDGET:4:10.00"),
    ("ORD-0005", "Barbara Liskov", datetime(2024, 3, 21, 0, 0, 0), OrderStatus.CANCELLED, "GADGET:1:25.00"),
    ("ORD-0006", "Donald Knuth", datetime(2024, 4, 2, 8, 0, 0), OrderStatus.SHIPPED, "BOOK:1:79.90"),
]

EXPECTED_UNFILTERED = (
    "id,created_at,customer,email,status,items,total\n"
    "ORD-0001,2024-03-09T23:59:59,Ada Lovelace,ord-0001@example.com,pending,1,10.00\n"
    "ORD-0002,2024-03-10T00:00:00,Grace Hopper,ord-0002@example.com,paid,2,50.00\n"
    "ORD-0003,2024-03-15T12:30:00,Alan Turing,ord-0003@example.com,shipped,3,13.50\n"
    "ORD-0004,2024-03-20T23:59:59,Edsger Dijkstra,ord-0004@example.com,shipped,4,40.00\n"
    "ORD-0005,2024-03-21T00:00:00,Barbara Liskov,ord-0005@example.com,cancelled,1,25.00\n"
    "ORD-0006,2024-04-02T08:00:00,Donald Knuth,ord-0006@example.com,shipped,1,79.90\n"
)


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "orders.csv")
    store = OrderStore(path)
    for order_id, customer, created, status, item in SEED:
        store.add(
            Order(
                id=order_id,
                customer=customer,
                email=f"{order_id.lower()}@example.com",
                created_at=created,
                status=status,
                items=[LineItem.from_spec(item)],
            )
        )
    return path


def run_cli(*args):
    env = dict(os.environ)
    env.pop("ORDERTOOL_DB", None)
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "ordertool", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def export_ids(db, *extra):
    result = run_cli("--db", db, "export", *extra)
    assert result.returncode == 0, result.stderr
    rows = list(csv.reader(io.StringIO(result.stdout)))
    assert rows[0] == ["id", "created_at", "customer", "email", "status", "items", "total"]
    return [row[0] for row in rows[1:]]


def assert_clean_error(result, *needles):
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "Traceback" not in result.stdout
    for needle in needles:
        assert needle.lower() in result.stderr.lower(), result.stderr


def test_no_filters_output_unchanged(db):
    result = run_cli("--db", db, "export")
    assert result.returncode == 0, result.stderr
    assert result.stdout == EXPECTED_UNFILTERED


def test_from_is_inclusive_and_starts_at_midnight(db):
    assert export_ids(db, "--from", "2024-03-10") == [
        "ORD-0002", "ORD-0003", "ORD-0004", "ORD-0005", "ORD-0006",
    ]


def test_to_is_inclusive_of_the_whole_day(db):
    # ORD-0004 was placed at 23:59:59 on the --to date and must be included.
    assert export_ids(db, "--to", "2024-03-20") == [
        "ORD-0001", "ORD-0002", "ORD-0003", "ORD-0004",
    ]


def test_date_range(db):
    assert export_ids(db, "--from", "2024-03-10", "--to", "2024-03-20") == [
        "ORD-0002", "ORD-0003", "ORD-0004",
    ]


def test_single_day_range(db):
    assert export_ids(db, "--from", "2024-03-21", "--to", "2024-03-21") == ["ORD-0005"]


def test_status_filter(db):
    assert export_ids(db, "--status", "shipped") == ["ORD-0003", "ORD-0004", "ORD-0006"]


def test_combined_date_and_status_filters(db):
    assert export_ids(
        db, "--from", "2024-03-10", "--to", "2024-03-31", "--status", "shipped"
    ) == ["ORD-0003", "ORD-0004"]


def test_filters_apply_when_writing_to_file(db, tmp_path):
    target = tmp_path / "march_shipped.csv"
    result = run_cli(
        "--db", db, "export", "--output", str(target),
        "--from", "2024-03-01", "--to", "2024-03-31", "--status", "shipped",
    )
    assert result.returncode == 0, result.stderr
    rows = list(csv.reader(io.StringIO(target.read_text())))
    assert [row[0] for row in rows[1:]] == ["ORD-0003", "ORD-0004"]


def test_no_matches_gives_header_only(db):
    assert export_ids(db, "--from", "2025-01-01") == []


@pytest.mark.parametrize("flag,value", [
    ("--from", "03/10/2024"),
    ("--to", "2024-02-30"),
    ("--from", "yesterday"),
])
def test_invalid_date_gives_clean_error(db, flag, value):
    result = run_cli("--db", db, "export", flag, value)
    assert_clean_error(result, value, "date")
    assert result.stdout.strip() == "" or "ORD-" not in result.stdout


def test_invalid_status_gives_clean_error(db):
    result = run_cli("--db", db, "export", "--status", "lost")
    assert_clean_error(result, "lost", "shipped")
    assert "ORD-" not in result.stdout


def test_main_entry_point_accepts_new_flags(db, capsys):
    from ordertool.cli import main

    assert main(["--db", db, "export", "--status", "paid", "--from", "2024-03-10"]) == 0
    out = capsys.readouterr().out
    ids = [row[0] for row in csv.reader(io.StringIO(out))][1:]
    assert ids == ["ORD-0002"]
