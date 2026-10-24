"""Feature 7: CSV / JSON export with filters."""
import csv
import io
import json

import pytest

from ft_helpers import day, txn


@pytest.fixture
def filled(ft, ledger):
    ledger.add_transactions([
        txn(ft, day(2026, 3, 31), "END, OF MONTH", "-3.00", category="Fun"),
        txn(ft, day(2026, 3, 1), "TESCO", "-45.20", category="Groceries"),
        txn(ft, day(2026, 3, 10), "MYSTERY", "-7.50"),
        txn(ft, day(2026, 4, 1), "APRIL", "1200", category="Salary", account="joint"),
    ])
    return ledger


def test_csv_export_all(ft, filled):
    buf = io.StringIO()
    n = ft.export.export_transactions(filled, buf, "csv")
    assert n == 4
    text = buf.getvalue()
    assert "\r\n" not in text
    assert text.splitlines()[0] == "date,description,amount,category,account"
    rows = list(csv.DictReader(io.StringIO(text)))
    assert [(r["date"], r["description"], r["amount"], r["category"], r["account"]) for r in rows] == [
        ("2026-03-01", "TESCO", "-45.20", "Groceries", "default"),
        ("2026-03-10", "MYSTERY", "-7.50", "", "default"),
        ("2026-03-31", "END, OF MONTH", "-3.00", "Fun", "default"),
        ("2026-04-01", "APRIL", "1200.00", "Salary", "joint"),
    ]


def test_json_export(ft, filled):
    buf = io.StringIO()
    assert ft.export.export_transactions(filled, buf, fmt="json") == 4
    data = json.loads(buf.getvalue())
    assert data[1] == {"date": "2026-03-10", "description": "MYSTERY", "amount": "-7.50",
                       "category": None, "account": "default"}
    assert data[3]["amount"] == "1200.00"
    assert set(data[0].keys()) == {"date", "description", "amount", "category", "account"}


def test_date_range_inclusive_and_category_filter(ft, filled):
    buf = io.StringIO()
    n = ft.export.export_transactions(filled, buf, "json", start=day(2026, 3, 10), end=day(2026, 3, 31))
    assert n == 2
    assert [t["description"] for t in json.loads(buf.getvalue())] == ["MYSTERY", "END, OF MONTH"]
    buf = io.StringIO()
    assert ft.export.export_transactions(filled, buf, "json", category="Uncategorized") == 1
    buf = io.StringIO()
    assert ft.export.export_transactions(filled, buf, "csv", start=day(2026, 3, 2),
                                         category="Groceries") == 0
    assert buf.getvalue().strip() == "date,description,amount,category,account"


def test_export_errors(ft, filled):
    with pytest.raises(ft.errors.ValidationError):
        ft.export.export_transactions(filled, io.StringIO(), "xml")
    with pytest.raises(ft.errors.ValidationError):
        ft.export.export_transactions(filled, io.StringIO(), "csv", start=day(2026, 4, 1), end=day(2026, 3, 1))
