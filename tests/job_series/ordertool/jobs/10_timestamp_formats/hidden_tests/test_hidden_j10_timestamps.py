"""Job 10: friendlier timestamp input, in the shared date helper (add + import), storage unchanged."""

import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from ordertool.cli import main  # noqa: E402
from ordertool.errors import ValidationError  # noqa: E402
from ordertool.storage import OrderStore  # noqa: E402
from ordertool.utils.dates import parse_timestamp  # noqa: E402


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.mark.parametrize("text,expected", [
    ("2024-03-10T14:05:07", datetime(2024, 3, 10, 14, 5, 7)),
    ("2024-03-10 14:05:07", datetime(2024, 3, 10, 14, 5, 7)),
    ("2024-03-10T14:05", datetime(2024, 3, 10, 14, 5)),
    ("2024-03-10 14:05", datetime(2024, 3, 10, 14, 5)),
    (" 2024-03-10 09:30 ", datetime(2024, 3, 10, 9, 30)),
    ("2024-03-10", datetime(2024, 3, 10)),
])
def test_helper_accepts_friendly_forms(text, expected):
    assert parse_timestamp(text) == expected


@pytest.mark.parametrize("text", ["2024-03-10 25:00", "2024-03-10T14", "10/03/2024 14:05", "2024-03-10  14:05x", ""])
def test_helper_still_rejects_garbage(text):
    with pytest.raises(ValidationError):
        parse_timestamp(text)


def test_add_with_space_and_no_seconds(tmp_path, capsys):
    db = str(tmp_path / "orders.csv")
    code, _, err = run(capsys, "--db", db, "add", "--customer", "Ada", "--email", "ada@example.com",
                       "--item", "WIDGET:1:1.00", "--created-at", "2024-03-10 14:05")
    assert code == 0, err
    assert OrderStore(db).get("ORD-0001").created_at == datetime(2024, 3, 10, 14, 5)
    # stored and shown exactly as before
    assert "2024-03-10T14:05:00" in open(db, encoding="utf-8").read()
    code, out, _ = run(capsys, "--db", db, "list")
    assert "2024-03-10T14:05:00" in out


def test_add_rejects_bad_time_cleanly(tmp_path, capsys):
    db = str(tmp_path / "orders.csv")
    code, _, err = run(capsys, "--db", db, "add", "--customer", "Ada", "--email", "ada@example.com",
                       "--item", "WIDGET:1:1.00", "--created-at", "2024-03-10 25:00")
    assert code == 1
    assert err.startswith("error: invalid timestamp")


def test_import_accepts_friendly_forms(tmp_path, capsys):
    db = str(tmp_path / "orders.csv")
    src = tmp_path / "shop.csv"
    src.write_text("customer,email,created_at,items\n"
                   "Ada,ada@example.com,2024-03-10 09:30,WIDGET:1:1.00\n"
                   "Bob,bob@example.com,2024-03-11T10:15,WIDGET:1:1.00\n", encoding="utf-8")
    code, out, err = run(capsys, "--db", db, "import", str(src))
    assert code == 0, err
    assert [o.created_at for o in OrderStore(db).load_all()] == [
        datetime(2024, 3, 10, 9, 30), datetime(2024, 3, 11, 10, 15)]


def test_import_bad_time_still_reports_line(tmp_path, capsys):
    db = str(tmp_path / "orders.csv")
    src = tmp_path / "shop.csv"
    src.write_text("customer,email,created_at,items\n"
                   "Ada,ada@example.com,2024-03-10 09:30,WIDGET:1:1.00\n"
                   "Bob,bob@example.com,2024-03-11 24:15,WIDGET:1:1.00\n", encoding="utf-8")
    code, _, err = run(capsys, "--db", db, "import", str(src))
    assert code == 1
    assert err.startswith("error: ") and "line 3" in err
