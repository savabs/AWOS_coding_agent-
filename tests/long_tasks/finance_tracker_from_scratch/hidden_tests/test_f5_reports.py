"""Feature 5: monthly per-category reports (text and JSON)."""
import json
from decimal import Decimal

import pytest

from ft_helpers import day, txn


@pytest.fixture
def march(ft, ledger):
    ledger.add_transactions([
        txn(ft, day(2026, 2, 28), "FEB", "-999.00", category="Groceries"),
        txn(ft, day(2026, 3, 1), "TESCO", "-45.20", category="Groceries"),
        txn(ft, day(2026, 3, 2), "SALARY", "2500.00", category="Salary"),
        txn(ft, day(2026, 3, 9), "TESCO", "-60.05", category="Groceries"),
        txn(ft, day(2026, 3, 12), "REFUND", "10.00", category="Groceries"),
        txn(ft, day(2026, 3, 20), "MYSTERY", "-7.50"),
        txn(ft, day(2026, 3, 31), "UBER", "-12.40", category="Transport"),
        txn(ft, day(2026, 4, 1), "APRIL", "-1.00", category="Transport"),
    ])
    return ledger


def test_monthly_totals_counts_and_order(ft, march):
    rep = ft.reports.monthly_report(march, 2026, 3)
    assert (rep.year, rep.month) == (2026, 3)
    assert [(r.category, r.count, r.total) for r in rep.rows] == [
        ("Groceries", 3, Decimal("-95.25")),
        ("Transport", 1, Decimal("-12.40")),
        ("Uncategorized", 1, Decimal("-7.50")),
        ("Salary", 1, Decimal("2500.00")),
    ]
    assert rep.income == Decimal("2510.00")
    assert rep.expenses == Decimal("-125.15")
    assert rep.net == Decimal("2384.85")


def test_render_text_exact(ft, march):
    text = ft.reports.render_text(ft.reports.monthly_report(march, 2026, 3))
    expected = "\n".join([
        "Report for 2026-03",
        f"{'Category':<20} {'Count':>5} {'Total':>12}",
        f"{'Groceries':<20} {3:>5} {'-95.25':>12}",
        f"{'Transport':<20} {1:>5} {'-12.40':>12}",
        f"{'Uncategorized':<20} {1:>5} {'-7.50':>12}",
        f"{'Salary':<20} {1:>5} {'2500.00':>12}",
        "Income: 2510.00",
        "Expenses: -125.15",
        "Net: 2384.85",
    ]) + "\n"
    assert text == expected


def test_render_json(ft, march):
    data = json.loads(ft.reports.render_json(ft.reports.monthly_report(march, 2026, 3)))
    assert data["month"] == "2026-03"
    assert data["categories"][0] == {"category": "Groceries", "count": 3, "total": "-95.25"}
    assert [c["category"] for c in data["categories"]] == ["Groceries", "Transport", "Uncategorized", "Salary"]
    assert (data["income"], data["expenses"], data["net"]) == ("2510.00", "-125.15", "2384.85")


def test_empty_month(ft, march):
    rep = ft.reports.monthly_report(march, 2026, 5)
    assert rep.rows == []
    assert rep.income == rep.expenses == rep.net == Decimal("0")
    assert ft.reports.render_text(rep) == "Report for 2026-05\nNo transactions.\n"
    data = json.loads(ft.reports.render_json(rep))
    assert data == {"month": "2026-05", "categories": [], "income": "0.00", "expenses": "0.00", "net": "0.00"}


def test_zero_totals_ties_and_bad_month(ft, ledger):
    ledger.add_transactions([
        txn(ft, day(2026, 6, 1), "BUY", "-20.00", category="Zeta"),
        txn(ft, day(2026, 6, 2), "RETURN", "20.00", category="Zeta"),
        txn(ft, day(2026, 6, 3), "B", "-5.00", category="Beta"),
        txn(ft, day(2026, 6, 4), "A", "-5.00", category="Alpha"),
    ])
    rep = ft.reports.monthly_report(ledger, 2026, 6)
    assert [(r.category, r.total) for r in rep.rows] == [
        ("Alpha", Decimal("-5.00")), ("Beta", Decimal("-5.00")), ("Zeta", Decimal("0.00"))]
    text = ft.reports.render_text(rep)
    assert "-0.00" not in text
    assert f"{'Zeta':<20} {2:>5} {'0.00':>12}" in text.splitlines()
    for bad in (0, 13):
        with pytest.raises(ft.errors.ValidationError):
            ft.reports.monthly_report(ledger, 2026, bad)
