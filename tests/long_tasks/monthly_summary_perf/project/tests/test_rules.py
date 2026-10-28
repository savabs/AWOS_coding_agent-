from datetime import date
from decimal import Decimal

import pytest

from ledger.models import Transaction
from ledger.rulebook import default_categoriser
from ledger.rules import Categoriser, Rule, merchant_name


def txn(desc, amount, currency="GBP"):
    return Transaction("T1", "A", date(2024, 1, 1), desc, Decimal(amount), currency)


def test_priority_then_name_order():
    cat = Categoriser([
        Rule("zeta", r"SHOP", "Z", 10),
        Rule("alpha", r"SHOP", "A", 10),
        Rule("first", r"SHOP", "F", 5, min_amount=Decimal("100")),
    ])
    assert cat.categorise(txn("THE SHOP", "-5.00")) == "A"
    assert cat.categorise(txn("THE SHOP", "-150.00")) == "F"
    assert [r.name for r in cat.ordered_rules()] == ["first", "alpha", "zeta"]


def test_direction_and_defaults():
    cat = Categoriser([Rule("refund", r"\bREFUND\b", "Refunds", 1, "credit")])
    assert cat.categorise(txn("REFUND ARGOS", "12.00")) == "Refunds"
    assert cat.categorise(txn("refund argos", "-12.00")) == "Uncategorised"
    assert cat.categorise(txn("SOMETHING", "3.00")) == "Other income"


def test_bad_rules_rejected():
    with pytest.raises(ValueError):
        Categoriser([Rule("x", "A", "B", direction="sideways")])


def test_default_rulebook_examples():
    cat = default_categoriser()
    assert cat.categorise(txn("CARD 1234 TESCO PETROL 0301 LEEDS", "-40.00")) == "Fuel"
    assert cat.categorise(txn("CARD 1234 TESCO 0301 LEEDS", "-40.00")) == "Groceries"
    assert cat.categorise(txn("CNP AMAZON 0555 ONLINE", "-300.00")) == "Shopping:Large"
    assert cat.categorise(txn("CNP AMAZON 0555 ONLINE", "-30.00")) == "Shopping"
    assert cat.categorise(txn("TFR TO SAVINGS 4411", "-300.00")) == "Savings"
    assert cat.categorise(txn("POS OAK GYM 0200 BATH", "-30.00")) == "Fitness"
    assert cat.categorise(txn("POS CORNER SHOP 0200 BATH", "-3.00")) == "Uncategorised"


@pytest.mark.parametrize("desc,expected", [
    ("CARD 4821 NORTH BAKERY 0231 LEEDS", "NORTH BAKERY"),
    ("POS WHOLE FOODS 0455 NEW YORK", "WHOLE FOODS"),
    ("TFR TO SAVINGS 1234", "TFR TO SAVINGS"),
    ("CNP AMAZON 0555 ONLINE", "AMAZON"),
    ("1234", "UNKNOWN"),
])
def test_merchant_name(desc, expected):
    assert merchant_name(desc) == expected
