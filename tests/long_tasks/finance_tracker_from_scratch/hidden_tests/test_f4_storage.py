"""Feature 4: SQLite persistence behind the Ledger API."""
from decimal import Decimal

import pytest

from ft_helpers import day, txn


def test_reopen_keeps_transactions_categories_and_budgets(ft, tmp_path):
    path = tmp_path / "data.db"
    with ft.storage.Ledger(path) as led:
        led.add_transactions([txn(ft, day(2026, 3, 1), "A", "-1.00"),
                              txn(ft, day(2026, 3, 2), "B", "2.00", account="savings")])
        first = led.transactions()[0]
        led.set_category(first.id, "Food")
        led.set_budget("Food", "150")
    assert path.exists()
    with ft.storage.Ledger(path) as led:
        got = led.transactions()
        assert [(t.date, t.description, t.amount, t.account, t.category) for t in got] == [
            (day(2026, 3, 1), "A", Decimal("-1.00"), "default", "Food"),
            (day(2026, 3, 2), "B", Decimal("2.00"), "savings", None),
        ]
        assert all(isinstance(t.id, int) for t in got)
        assert led.get_budgets() == {"Food": Decimal("150.00")}


def test_money_round_trips_exactly(ft, ledger):
    ledger.add_transactions([txn(ft, day(2026, 3, 1), "X", "0.10"),
                             txn(ft, day(2026, 3, 1), "Y", "0.20"),
                             txn(ft, day(2026, 3, 1), "Z", "-12345678.91")])
    got = ledger.transactions()
    assert all(isinstance(t.amount, Decimal) for t in got)
    assert got[0].amount + got[1].amount == Decimal("0.30")
    assert got[2].amount == Decimal("-12345678.91")


def test_query_filters_and_ordering(ft, ledger):
    ledger.add_transactions([
        txn(ft, day(2026, 3, 15), "LATE", "-1.00", category="Food"),
        txn(ft, day(2026, 3, 1), "EARLY", "-1.00"),
        txn(ft, day(2026, 3, 15), "LATE2", "-2.00", account="card"),
        txn(ft, day(2026, 3, 31), "END", "-3.00", category="Fun"),
        txn(ft, day(2026, 4, 1), "APRIL", "-4.00", category="Food"),
    ])
    assert [t.description for t in ledger.transactions()] == ["EARLY", "LATE", "LATE2", "END", "APRIL"]
    assert [t.description for t in ledger.transactions(start=day(2026, 3, 15), end=day(2026, 3, 31))] == \
        ["LATE", "LATE2", "END"]
    assert [t.description for t in ledger.transactions(category="Food")] == ["LATE", "APRIL"]
    assert [t.description for t in ledger.transactions(category="Uncategorized")] == ["EARLY", "LATE2"]
    assert [t.description for t in ledger.transactions(account="card")] == ["LATE2"]


def test_set_category_and_budget_validation(ft, ledger):
    ledger.add_transactions([txn(ft, day(2026, 3, 1), "A", "-1.00")])
    with pytest.raises(KeyError):
        ledger.set_category(99999, "Food")
    for bad in ("0", "-5", Decimal("-0.01"), "lots"):
        with pytest.raises(ft.errors.ValidationError):
            ledger.set_budget("Food", bad)
    ledger.set_budget("Food", Decimal("100"))
    ledger.set_budget("Food", 120)
    ledger.set_budget("Fun", "30.5")
    assert ledger.get_budgets() == {"Food": Decimal("120.00"), "Fun": Decimal("30.50")}
    assert ledger.remove_budget("Fun") is True
    assert ledger.remove_budget("Fun") is False
    assert ledger.get_budgets() == {"Food": Decimal("120.00")}
