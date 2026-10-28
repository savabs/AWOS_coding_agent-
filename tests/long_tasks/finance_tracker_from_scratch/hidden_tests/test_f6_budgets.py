"""Feature 6: per-category budgets with near/over alerts."""
from decimal import Decimal

import pytest

from ft_helpers import day, txn


@pytest.fixture
def budgeted(ft, ledger):
    ledger.add_transactions([
        txn(ft, day(2026, 3, 1), "TESCO", "-300.00", category="Groceries"),
        txn(ft, day(2026, 3, 15), "TESCO", "-150.00", category="Groceries"),
        txn(ft, day(2026, 3, 20), "UBER", "-85.00", category="Transport"),
        txn(ft, day(2026, 3, 5), "CINEMA", "-10.00", category="Fun"),
        txn(ft, day(2026, 4, 2), "TESCO", "-999.00", category="Groceries"),
    ])
    ledger.set_budget("Groceries", "400")
    ledger.set_budget("Transport", "100")
    ledger.set_budget("Fun", "50")
    ledger.set_budget("Clothes", "80")
    return ledger


def test_statuses_ordered_by_category(ft, budgeted):
    got = ft.budgets.check_budgets(budgeted, 2026, 3)
    assert [(s.category, s.budget, s.spent, s.remaining, s.status) for s in got] == [
        ("Clothes", Decimal("80.00"), Decimal("0.00"), Decimal("80.00"), "ok"),
        ("Fun", Decimal("50.00"), Decimal("10.00"), Decimal("40.00"), "ok"),
        ("Groceries", Decimal("400.00"), Decimal("450.00"), Decimal("-50.00"), "over"),
        ("Transport", Decimal("100.00"), Decimal("85.00"), Decimal("15.00"), "near"),
    ]


def test_exactly_at_budget_is_near_not_over(ft, ledger):
    ledger.add_transactions([txn(ft, day(2026, 3, 1), "GYM", "-40.00", category="Health")])
    ledger.set_budget("Health", "40")
    (s,) = ft.budgets.check_budgets(ledger, 2026, 3)
    assert (s.spent, s.remaining, s.status) == (Decimal("40.00"), Decimal("0.00"), "near")


def test_threshold_boundary_and_custom_threshold(ft, ledger):
    ledger.add_transactions([txn(ft, day(2026, 3, 1), "BOOKS", "-79.99", category="Books"),
                             txn(ft, day(2026, 3, 1), "MAGS", "-80.00", category="Mags")])
    ledger.set_budget("Books", "100")
    ledger.set_budget("Mags", "100")
    got = {s.category: s.status for s in ft.budgets.check_budgets(ledger, 2026, 3)}
    assert got == {"Books": "ok", "Mags": "near"}
    got = {s.category: s.status for s in
           ft.budgets.check_budgets(ledger, 2026, 3, near_threshold=Decimal("0.5"))}
    assert got == {"Books": "near", "Mags": "near"}


def test_refunds_reduce_spending_and_never_negative(ft, ledger):
    ledger.add_transactions([
        txn(ft, day(2026, 3, 1), "SHOES", "-120.00", category="Clothes"),
        txn(ft, day(2026, 3, 9), "SHOES REFUND", "100.00", category="Clothes"),
        txn(ft, day(2026, 3, 9), "GIFT CARD REFUND", "30.00", category="Gifts"),
    ])
    ledger.set_budget("Clothes", "50")
    ledger.set_budget("Gifts", "20")
    got = {s.category: (s.spent, s.status) for s in ft.budgets.check_budgets(ledger, 2026, 3)}
    assert got == {"Clothes": (Decimal("20.00"), "ok"), "Gifts": (Decimal("0.00"), "ok")}


def test_no_budgets_and_bad_month(ft, ledger):
    assert ft.budgets.check_budgets(ledger, 2026, 3) == []
    with pytest.raises(ft.errors.ValidationError):
        ft.budgets.check_budgets(ledger, 2026, 13)
