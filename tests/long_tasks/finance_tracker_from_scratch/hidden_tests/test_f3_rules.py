"""Feature 3: rule-based categorization."""
import json

import pytest

from ft_helpers import day, txn

RULES = {
    "default": "Other",
    "rules": [
        {"category": "Groceries", "contains": "tesco", "priority": 10},
        {"category": "Transport", "regex": "^(uber|lyft)\\b", "priority": 5},
        {"category": "Salary", "contains": "salary", "direction": "in"},
        {"category": "Fuel", "contains": "shell"},
        {"category": "Petrol", "contains": "shell"},
    ],
}


def _rules(ft, data=RULES):
    return ft.rules.RuleSet.from_dict(data)


def test_contains_is_case_insensitive_and_default(ft):
    rs = _rules(ft)
    assert rs.categorize(txn(ft, day(2026, 3, 1), "Tesco Express", "-3.00")) == "Groceries"
    assert rs.categorize(txn(ft, day(2026, 3, 1), "NETFLIX", "-9.99")) == "Other"
    assert rs.default == "Other"


def test_regex_rule(ft):
    rs = _rules(ft)
    assert rs.categorize(txn(ft, day(2026, 3, 1), "UBER TRIP 8812", "-12.40")) == "Transport"
    assert rs.categorize(txn(ft, day(2026, 3, 1), "lyft ride", "-8.00")) == "Transport"
    # anchored at the start and needs a word boundary
    assert rs.categorize(txn(ft, day(2026, 3, 1), "UBEREATS ORDER", "-20.00")) == "Other"
    assert rs.categorize(txn(ft, day(2026, 3, 1), "PAYPAL *UBER", "-20.00")) == "Other"


def test_priority_then_file_order(ft):
    rs = _rules(ft)
    # "TESCO" (prio 10) beats "UBER" regex (prio 5)
    assert rs.categorize(txn(ft, day(2026, 3, 1), "UBER to TESCO", "-1.00")) == "Groceries"
    # equal priority 0: the rule that appears first in the file wins
    assert rs.categorize(txn(ft, day(2026, 3, 1), "SHELL GARAGE", "-50.00")) == "Fuel"
    reordered = _rules(ft, {"rules": [{"category": "B", "contains": "x"},
                                      {"category": "A", "contains": "x", "priority": 1},
                                      {"category": "C", "contains": "x", "priority": 1}]})
    assert reordered.categorize(txn(ft, day(2026, 3, 1), "x", "-1.00")) == "A"


def test_direction_and_missing_default(ft):
    rs = _rules(ft)
    assert rs.categorize(txn(ft, day(2026, 3, 1), "ACME SALARY", "2500.00")) == "Salary"
    assert rs.categorize(txn(ft, day(2026, 3, 1), "SALARY ADVANCE REPAYMENT", "-100.00")) == "Other"
    nodefault = _rules(ft, {"rules": [{"category": "Fun", "contains": "cinema"}]})
    assert nodefault.categorize(txn(ft, day(2026, 3, 1), "BAKERY", "-2.00")) == "Uncategorized"


@pytest.mark.parametrize("bad", [
    "not json at all",
    json.dumps([1, 2, 3]),
    json.dumps({"rules": "nope"}),
    json.dumps({"rules": [{"contains": "x"}]}),
    json.dumps({"rules": [{"category": "A"}]}),
    json.dumps({"rules": [{"category": "A", "contains": "x", "regex": "x"}]}),
    json.dumps({"rules": [{"category": "A", "regex": "([unclosed"}]}),
    json.dumps({"rules": [{"category": "A", "contains": "x", "priority": "high"}]}),
    json.dumps({"rules": [{"category": "A", "contains": "x", "direction": "sideways"}]}),
])
def test_invalid_rules_files(ft, write, bad):
    with pytest.raises(ft.errors.RulesError):
        ft.rules.load_rules(write("rules.json", bad))


def test_categorize_ledger_only_uncategorized_unless_recategorize(ft, write, ledger):
    ledger.add_transactions([
        txn(ft, day(2026, 3, 1), "TESCO", "-10.00"),
        txn(ft, day(2026, 3, 2), "UBER", "-5.00"),
        txn(ft, day(2026, 3, 3), "TESCO METRO", "-7.00", category="Manual"),
    ])
    rs = ft.rules.load_rules(write("rules.json", json.dumps(RULES)))
    assert ft.rules.categorize_ledger(ledger, rs) == 2
    assert [t.category for t in ledger.transactions()] == ["Groceries", "Transport", "Manual"]
    assert ft.rules.categorize_ledger(ledger, rs) == 0
    assert ft.rules.categorize_ledger(ledger, rs, recategorize=True) == 3
    assert [t.category for t in ledger.transactions()] == ["Groceries", "Transport", "Groceries"]
