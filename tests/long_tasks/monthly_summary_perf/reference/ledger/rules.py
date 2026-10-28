"""Categorisation rules and merchant-name normalisation.

A rule matches a transaction when its regular expression is found in the
(case-insensitive) description and its optional direction / minimum amount
conditions hold.  Rules are evaluated in order of ``(priority, name)`` --
lower priority numbers first, ties broken alphabetically by rule name -- and
the first matching rule decides the category.

Patterns are compiled once; the rule order is computed once per rule set;
and for each distinct description we remember which rules' patterns match,
so repeated descriptions only re-check the cheap direction/amount filters.
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

_COMPILED = {}


def _compiled(pattern):
    regex = _COMPILED.get(pattern)
    if regex is None:
        regex = _COMPILED[pattern] = re.compile(pattern, re.IGNORECASE)
    return regex


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: str
    category: str
    priority: int = 100
    direction: str = "any"  # "any", "debit" or "credit"
    min_amount: Decimal = None  # absolute amount, in the transaction currency

    def accepts_amount(self, amount):
        if self.direction == "debit" and amount >= 0:
            return False
        if self.direction == "credit" and amount < 0:
            return False
        if self.min_amount is not None and abs(amount) < self.min_amount:
            return False
        return True

    def matches(self, description, amount):
        if not self.accepts_amount(amount):
            return False
        return _compiled(self.pattern).search(description) is not None


class Categoriser:
    def __init__(self, rules=(), default_debit=DEFAULT_DEBIT_CATEGORY,
                 default_credit=DEFAULT_CREDIT_CATEGORY):
        self.rules = []
        self.default_debit = default_debit
        self.default_credit = default_credit
        self._snapshot = None
        self._ordered = []
        self._compiled = []
        self._hits = {}
        for rule in rules:
            self.add_rule(rule)

    def add_rule(self, rule):
        if rule.direction not in ("any", "debit", "credit"):
            raise ValueError(f"bad direction {rule.direction!r} in rule {rule.name}")
        re.compile(rule.pattern)  # fail fast on invalid patterns
        self.rules.append(rule)

    def _refresh(self):
        snapshot = tuple(self.rules)
        if snapshot != self._snapshot:
            self._snapshot = snapshot
            self._ordered = sorted(snapshot, key=lambda r: (r.priority, r.name))
            self._compiled = [_compiled(r.pattern) for r in self._ordered]
            self._hits = {}

    def ordered_rules(self):
        self._refresh()
        return list(self._ordered)

    def categorise(self, txn):
        self._refresh()
        description = txn.description
        hits = self._hits.get(description)
        if hits is None:
            hits = tuple(rule for rule, regex in zip(self._ordered, self._compiled)
                         if regex.search(description) is not None)
            self._hits[description] = hits
        amount = txn.amount
        for rule in hits:
            if rule.accepts_amount(amount):
                return rule.category
        return self.default_debit if amount < 0 else self.default_credit


_PREFIX_RE = re.compile(r"^(?:" + "|".join(_CHANNEL_PREFIXES) + r")\b\s*(?:\d{4}\b)?\s*")
_CITY_RE = re.compile(r"\b(?:" + "|".join(re.escape(c) for c in CITIES) + r")\b")
_DIGITS_RE = re.compile(r"#?\b\w*\d\w*\b")


def merchant_name(description):
    """Reduce a raw description to a stable merchant name.

    ``"CARD 4821 NORTH BAKERY 0231 LEEDS"`` -> ``"NORTH BAKERY"``
    """
    text = description.upper()
    text = _PREFIX_RE.sub("", text)
    text = _CITY_RE.sub(" ", text)
    text = _DIGITS_RE.sub(" ", text)
    text = " ".join(text.split())
    return text or "UNKNOWN"
