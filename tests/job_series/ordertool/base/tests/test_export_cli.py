import csv
import io

from ordertool.cli import main
from ordertool.export import EXPORT_COLUMNS, export_line_items_csv, export_orders_csv
from ordertool.reports import render_summary


def test_export_orders_csv(seeded):
    buf = io.StringIO()
    count = export_orders_csv(seeded.list_orders(), buf)
    rows = list(csv.reader(io.StringIO(buf.getvalue())))
    assert count == 3
    assert rows[0] == EXPORT_COLUMNS
    assert rows[2] == ["ORD-0002", "2024-03-05T17:40:00", "Grace Hopper",
                       "grace@example.com", "pending", "4", "38.50"]


def test_export_line_items(seeded):
    buf = io.StringIO()
    assert export_line_items_csv(seeded.list_orders(), buf) == 4


def test_cli_add_and_list(db_path, capsys):
    assert main(["--db", db_path, "add", "--customer", "Ada", "--email", "ada@example.com",
                 "--item", "WIDGET:2:9.99", "--created-at", "2024-01-02T03:04:05"]) == 0
    assert "created ORD-0001 (19.98)" in capsys.readouterr().out
    assert main(["--db", db_path, "list"]) == 0
    assert "2024-01-02T03:04:05" in capsys.readouterr().out


def test_cli_export_to_file(seeded, db_path, tmp_path):
    target = tmp_path / "out.csv"
    assert main(["--db", db_path, "export", "--output", str(target)]) == 0
    assert target.read_text().count("\n") == 4


def test_cli_reports_user_errors_cleanly(seeded, db_path, capsys):
    assert main(["--db", db_path, "set-status", "ORD-0001", "delivered"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: cannot change ORD-0001")


def test_report_summary(seeded):
    text = render_summary(seeded.list_orders())
    assert "Orders: 3" in text
    assert "2024-03-05" in text
