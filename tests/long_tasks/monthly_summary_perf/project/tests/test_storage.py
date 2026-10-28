from datetime import date
from decimal import Decimal

import pytest

from ledger.models import Account
from ledger.storage import LedgerStore, UnknownAccountError


def row(txn_id, day, amount, account="A", desc="X", currency="GBP"):
    return {"id": txn_id, "account": account, "date": day, "desc": desc,
            "amount": amount, "currency": currency}


@pytest.fixture
def store():
    s = LedgerStore()
    s.add_account(Account("A", "Ann", "GBP", Decimal("10.00"), date(2024, 1, 1)))
    s.add_row(row("A-3", "2024-01-05", "-1.00"))
    s.add_row(row("A-1", "2024-01-03", "2.00", desc="  PADDED   DESC "))
    s.add_row(row("B-1", "2024-01-04", "5.00", account="B"))
    s.add_row(row("A-2", "2024-01-05", "(3.50)"))
    return s


def test_posting_order_and_filtering(store):
    ids = [t.txn_id for t in store.transactions("A")]
    assert ids == ["A-1", "A-2", "A-3"]
    assert store.transactions("A")[0].description == "PADDED DESC"
    assert store.transactions("A")[1].amount == Decimal("-3.50")


def test_inclusive_ranges(store):
    ids = [t.txn_id for t in store.transactions("A", start=date(2024, 1, 5))]
    assert ids == ["A-2", "A-3"]
    ids = [t.txn_id for t in store.transactions("A", end=date(2024, 1, 3))]
    assert ids == ["A-1"]
    assert store.transactions("A", start=date(2024, 1, 6)) == []


def test_get_and_accounts(store):
    assert store.get("B-1").amount == Decimal("5.00")
    with pytest.raises(KeyError):
        store.get("nope")
    with pytest.raises(UnknownAccountError):
        store.account("B")
    assert len(store) == 4


def test_new_rows_are_visible(store):
    assert len(store.transactions("A")) == 3
    store.add_row(row("A-0", "2024-01-01", "1.00"))
    assert [t.txn_id for t in store.transactions("A")][0] == "A-0"


def test_csv_roundtrip(store, tmp_path):
    path = tmp_path / "ledger.csv"
    store.save_csv(path)
    other = LedgerStore()
    other.load_csv(path)
    assert other.transactions("A") == store.transactions("A")


def test_missing_fields_rejected(store):
    with pytest.raises(ValueError):
        store.add_row({"id": "x"})
