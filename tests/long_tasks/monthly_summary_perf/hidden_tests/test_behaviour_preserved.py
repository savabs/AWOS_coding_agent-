"""Behaviour must match the original implementation, including after the
data changes between calls (late postings, FX corrections, new rules), so
any caching has to stay correct."""

from datetime import date, timedelta
from decimal import Decimal

import slow_ledger.fixtures as slow_fixtures
import slow_ledger.rules as slow_rules
import slow_ledger.summary as slow_summary

import ledger.fixtures as fixtures
import ledger.rules as rules
import ledger.summary as summary


def _pair(seed, size):
    return fixtures.generate_ledger(seed, size), slow_fixtures.generate_ledger(seed, size)


def _both(new, old, account_attr, year, month):
    got = summary.build_monthly_summary(new.store, new.fx, new.categoriser,
                                        getattr(new, account_attr), year, month).to_dict()
    want = slow_summary.build_monthly_summary(old.store, old.fx, old.categoriser,
                                              getattr(old, account_attr), year, month).to_dict()
    return got, want


def test_late_postings_are_picked_up_after_a_summary():
    new, old = _pair(21, "small")
    got, want = _both(new, old, "account_id", 2025, 4)
    assert got == want
    late = [
        {"id": "ZZ-late-1", "account": new.account_id, "date": "2025-04-17",
         "desc": "CARD 9999 CARREFOUR 0300 PARIS", "amount": "-87.35", "currency": "EUR"},
        {"id": "AA-late-2", "account": new.account_id, "date": "2025-04-17",
         "desc": "  POS   OAK GYM 0200 BATH ", "amount": "(412.99)", "currency": "GBP"},
        {"id": "ZZ-late-3", "account": new.account_id, "date": "2025-03-02",
         "desc": "FPI J SMITH REF 12", "amount": "1,250.00", "currency": "GBP"},
        {"id": "ZZ-late-4", "account": new.account_id, "date": "2025-04-30",
         "desc": "CNP WHOLE FOODS 0455 BOSTON", "amount": "-19.99", "currency": "USD"},
    ]
    for row in late:
        new.store.add_row(dict(row))
        old.store.add_row(dict(row))
    got, want = _both(new, old, "account_id", 2025, 4)
    assert got == want
    assert got["txn_count"] == want["txn_count"]


def test_fx_corrections_are_picked_up_after_a_summary():
    new, old = _pair(22, "small")
    assert _both(new, old, "account_id", 2025, 6)[0] == _both(new, old, "account_id", 2025, 6)[1]
    day = date(2025, 5, 30)
    for cur, rate in (("EUR", "0.912345"), ("USD", "0.701234"), ("SEK", "0.081111"), ("CHF", "0.955555")):
        for table in (new.fx, old.fx):
            table.add_rate(day, cur, rate)
            table.add_rate(day + timedelta(days=5), cur, rate)
    got, want = _both(new, old, "account_id", 2025, 6)
    assert got == want


def test_rule_changes_are_picked_up_after_a_summary():
    new, old = _pair(23, "small")
    assert _both(new, old, "account_id", 2025, 8)[0] == _both(new, old, "account_id", 2025, 8)[1]
    new.categoriser.add_rule(rules.Rule("aaa-bakery", r"BAKERY", "Treats", 50, "debit"))
    old.categoriser.add_rule(slow_rules.Rule("aaa-bakery", r"BAKERY", "Treats", 50, "debit"))
    new.categoriser.add_rule(rules.Rule("big-groceries", r"TESCO|LIDL|CARREFOUR", "Big shop", 1,
                                        "debit", Decimal("40")))
    old.categoriser.add_rule(slow_rules.Rule("big-groceries", r"TESCO|LIDL|CARREFOUR", "Big shop", 1,
                                             "debit", Decimal("40")))
    got, want = _both(new, old, "account_id", 2025, 8)
    assert got == want


def test_joint_account_and_early_months_match():
    new, old = _pair(24, "medium")
    for year, month in ((2024, 1), (2024, 2), (2025, 1), (2025, 9)):
        got, want = _both(new, old, "joint_account_id", year, month)
        assert got == want, (year, month)
        got, want = _both(new, old, "account_id", year, month)
        assert got == want, (year, month)


def test_store_and_fx_queries_match_original():
    new, old = _pair(25, "small")
    ranges = [(None, None), (date(2024, 5, 1), date(2024, 5, 31)), (None, date(2024, 2, 29)),
              (date(2025, 12, 31), None), (date(2025, 7, 4), date(2025, 7, 4))]
    for start, end in ranges:
        got = [t.to_row() for t in new.store.transactions(new.account_id, start=start, end=end)]
        want = [t.to_row() for t in old.store.transactions(old.account_id, start=start, end=end)]
        assert got == want, (start, end)
    day = date(2024, 1, 1)
    while day <= date(2025, 12, 31):
        for cur in ("EUR", "USD", "CHF", "SEK", "GBP"):
            assert new.fx.rate(cur, day) == old.fx.rate(cur, day), (cur, day)
        day += timedelta(days=3)


def test_categories_and_merchants_match_original():
    new, old = _pair(26, "small")
    for txn in old.store.transactions(old.account_id):
        assert new.categoriser.categorise(txn) == old.categoriser.categorise(txn), txn
        assert rules.merchant_name(txn.description) == slow_rules.merchant_name(txn.description)
