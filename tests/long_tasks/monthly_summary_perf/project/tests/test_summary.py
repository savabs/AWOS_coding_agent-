from datetime import date
from decimal import Decimal

import pytest

from ledger.fixtures import generate_ledger
from ledger.fx import FxTable
from ledger.models import Account
from ledger.rulebook import default_categoriser
from ledger.storage import LedgerStore
from ledger.summary import build_monthly_summary

D = Decimal


@pytest.fixture
def hand_ledger():
    store = LedgerStore()
    store.add_account(Account("A", "Ann", "GBP", D("100.00"), date(2024, 1, 1)))
    rows = [
        ("A-1", "2024-01-15", "FPI ACME LTD SALARY", "1000.00", "GBP"),
        ("A-2", "2024-02-01", "SO LANDLORD RENT FLAT 2", "-600.00", "GBP"),
        ("A-3", "2024-02-03", "CARD 1111 CARREFOUR 0300 PARIS", "-20.00", "EUR"),
        ("A-4", "2024-02-03", "CARD 1111 NORTH BAKERY 0101 LEEDS", "-3.50", "GBP"),
        ("A-5", "2024-02-10", "REFUND ARGOS 123", "15.00", "GBP"),
        ("A-6", "2024-02-29", "POS WHOLE FOODS 0455 NEW YORK", "-10.00", "USD"),
    ]
    for txn_id, day, desc, amount, cur in rows:
        store.add_row({"id": txn_id, "account": "A", "date": day, "desc": desc,
                       "amount": amount, "currency": cur})
    fx = FxTable("GBP")
    fx.add_rate("2024-01-31", "EUR", "0.850000")
    fx.add_rate("2024-02-02", "EUR", "0.860000")
    fx.add_rate("2024-01-31", "USD", "0.790000")
    return store, fx, default_categoriser()


def test_hand_calculated_summary(hand_ledger):
    store, fx, cat = hand_ledger
    s = build_monthly_summary(store, fx, cat, "A", 2024, 2)
    assert s.opening_balance == D("1100.00")
    assert s.total_income == D("15.00")
    assert s.total_spending == D("628.60")
    assert s.net_movement == D("-613.60")
    assert s.closing_balance == D("486.40")
    assert s.fx_fees == D("0.69")
    assert (s.txn_count, s.debit_count) == (5, 4)
    assert s.average_spend == D("157.15")
    assert s.largest_expense == ("A-2", D("600.00"))
    assert s.categories == {"Housing": D("-600.00"), "Groceries": D("-25.10"),
                            "Eating out": D("-3.50"), "Refunds": D("15.00")}
    assert s.ytd_categories["Income:Salary"] == D("1000.00")
    assert s.top_merchants == [("LANDLORD RENT FLAT", D("600.00")), ("CARREFOUR", D("17.20")),
                               ("WHOLE FOODS", D("7.90")), ("NORTH BAKERY", D("3.50"))]
    balances = dict(s.daily_balances)
    assert len(balances) == 29
    assert balances[date(2024, 2, 1)] == D("500.00")
    assert balances[date(2024, 2, 2)] == D("500.00")
    assert balances[date(2024, 2, 3)] == D("479.30")
    assert balances[date(2024, 2, 10)] == D("494.30")


def test_generated_ledger_is_consistent():
    bundle = generate_ledger(5, "tiny")
    s = build_monthly_summary(bundle.store, bundle.fx, bundle.categoriser,
                              bundle.account_id, 2025, 3)
    assert s.closing_balance == s.opening_balance + s.net_movement
    assert sum(s.categories.values()) == s.net_movement
    assert s.daily_balances[-1][1] == s.closing_balance
    assert s.to_dict()["period"] == "2025-03"


def test_generator_is_deterministic():
    a = generate_ledger(9, "tiny")
    b = generate_ledger(9, "tiny")
    assert a.store.transactions(a.account_id) == b.store.transactions(b.account_id)


def test_currency_mismatch_rejected(hand_ledger):
    store, _, cat = hand_ledger
    with pytest.raises(ValueError):
        build_monthly_summary(store, FxTable("EUR"), cat, "A", 2024, 2)
