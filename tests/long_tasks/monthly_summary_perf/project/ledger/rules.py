"""Categorisation rules and merchant-name normalisation.

A rule matches a transaction when its regular expression is found in the
(case-insensitive) description and its optional direction / minimum amount
conditions hold.  Rules are evaluated in order of ``(priority, name)`` --
lower priority numbers first, ties broken alphabetically by rule name -- and
the first matching rule decides the category.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

DEFAULT_DEBIT_CATEGORY = "Uncategorised"
DEFAULT_CREDIT_CATEGORY = "Other income"

CITIES = (
    "LONDON", "LEEDS", "BRISTOL", "YORK", "BATH", "MANCHESTER", "GLASGOW",
    "CARDIFF", "OXFORD", "CAMBRIDGE", "BRIGHTON", "NORWICH", "PARIS", "BERLIN",
    "ZURICH", "STOCKHOLM", "NEW YORK", "BOSTON", "MADRID", "ONLINE",
)
_CHANNEL_PREFIXES = ("CARD", "POS", "DD", "SO", "FPI", "FPO", "CNP")


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: str
    category: str
    priority: int = 100
    direction: str = "any"  # "any", "debit" or "credit"
    min_amount: Decimal = None  # absolute amount, in the transaction currency

    def matches(self, description, amount):
        if self.direction == "debit" and amount >= 0:
            return False
        if self.direction == "credit" and amount < 0:
            return False
        if self.min_amount is not None and abs(amount) < self.min_amount:
            return False
        return re.compile(self.pattern, re.IGNORECASE).search(description) is not None


class Categoriser:
    def __init__(self, rules=(), default_debit=DEFAULT_DEBIT_CATEGORY,
                 default_credit=DEFAULT_CREDIT_CATEGORY):
        self.rules = []
        self.default_debit = default_debit
        self.default_credit = default_credit
        for rule in rules:
            self.add_rule(rule)

    def add_rule(self, rule):
        if rule.direction not in ("any", "debit", "credit"):
            raise ValueError(f"bad direction {rule.direction!r} in rule {rule.name}")
        re.compile(rule.pattern)  # fail fast on invalid patterns
        self.rules.append(rule)

    def ordered_rules(self):
        return sorted(self.rules, key=lambda r: (r.priority, r.name))

    def categorise(self, txn):
        for rule in self.ordered_rules():
            if rule.matches(txn.description, txn.amount):
                return rule.category
        return self.default_debit if txn.amount < 0 else self.default_credit


def merchant_name(description):
    """Reduce a raw description to a stable merchant name.

    ``"CARD 4821 NORTH BAKERY 0231 LEEDS"`` -> ``"NORTH BAKERY"``
    """
    text = description.upper()
    prefix = r"^(?:" + "|".join(_CHANNEL_PREFIXES) + r")\b\s*(?:\d{4}\b)?\s*"
    text = re.sub(prefix, "", text)
    text = re.sub(r"\b(?:" + "|".join(re.escape(c) for c in CITIES) + r")\b", " ", text)
    text = re.sub(r"#?\b\w*\d\w*\b", " ", text)
    text = " ".join(text.split())
    return text or "UNKNOWN"
