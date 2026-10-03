"""Job 09: bad --now / import path / --db give the usual one-line error (stdout, exit 2)."""

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
        SettingsStore(storage).register("acme", "Acme", "UTC", "USD")
        storage.add_order(Order("a1", "acme", 1000, datetime(2026, 3, 10, 12, 0, tzinfo=UTC)))
    return path


def run(capsys, *argv):
    out = io.StringIO()
    code = main(list(argv), out)
    captured = capsys.readouterr()
    assert captured.err == "", captured.err
    assert captured.out == ""
    return code, out.getvalue()


def assert_one_line_error(code, out, *mentions):
    assert code == 2
    assert out.startswith("error: ")
    assert out.endswith("\n") and out.count("\n") == 1
    for m in mentions:
        assert m in out


@pytest.mark.parametrize("value", ["garbage", "2026-13-01T00:00:00Z", "yesterday"])
def test_bad_now(db, capsys, value):
    code, out = run(capsys, "--db", db, "--now", value, "report", "acme")
    assert_one_line_error(code, out, value)


def test_good_now_still_works(db, capsys):
    code, out = run(capsys, "--db", db, "--now", "2026-03-10T20:00:00Z", "report", "acme")
    assert code == 0
    assert "Total:     $10.00" in out


def test_import_of_a_missing_file(db, capsys, tmp_path):
    missing = str(tmp_path / "nope.csv")
    code, out = run(capsys, "--db", db, "import", missing)
    assert_one_line_error(code, out, missing)


def test_import_of_a_folder(db, capsys, tmp_path):
    folder = tmp_path / "folder"
    folder.mkdir()
    code, out = run(capsys, "--db", db, "import", str(folder))
    assert_one_line_error(code, out, str(folder))


def test_db_is_a_folder(capsys, tmp_path):
    code, out = run(capsys, "--db", str(tmp_path), "report", "acme")
    assert_one_line_error(code, out, str(tmp_path))


def test_db_in_a_missing_folder(capsys, tmp_path):
    path = str(tmp_path / "no" / "such" / "s.db")
    code, out = run(capsys, "--db", path, "report", "acme")
    assert_one_line_error(code, out, path)


def test_db_is_not_a_database(capsys, tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("these are my notes, not a database\n" * 50)
    code, out = run(capsys, "--db", str(path), "report", "acme")
    assert_one_line_error(code, out, str(path))
