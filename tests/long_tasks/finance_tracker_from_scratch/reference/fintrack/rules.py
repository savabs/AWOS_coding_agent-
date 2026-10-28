"""Rule-based categorization."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from fintrack.errors import RulesError
from fintrack.models import Transaction

DEFAULT_CATEGORY = "Uncategorized"


@dataclass
class Rule:
    category: str
    pattern: str
    kind: str
    priority: int = 0
    direction: Optional[str] = None
    _regex: Optional[re.Pattern] = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if self.kind == "regex":
            try:
                self._regex = re.compile(self.pattern, re.IGNORECASE)
            except re.error as exc:
                raise RulesError(f"invalid regex {self.pattern!r}: {exc}") from None
        elif self.kind != "contains":
            raise RulesError(f"unknown rule kind {self.kind!r}")

    def matches(self, txn: Transaction) -> bool:
        if self.direction == "out" and not txn.amount < 0:
            return False
        if self.direction == "in" and not txn.amount > 0:
            return False
        if self.kind == "contains":
            return self.pattern.casefold() in txn.description.casefold()
        return self._regex.search(txn.description) is not None


class RuleSet:
    def __init__(self, rules: List[Rule], default: str = DEFAULT_CATEGORY):
        self.rules = list(rules)
        self.default = default

    @classmethod
    def from_dict(cls, data) -> "RuleSet":
        if not isinstance(data, dict):
            raise RulesError("rules file must contain a JSON object")
        default = data.get("default", DEFAULT_CATEGORY)
        if not isinstance(default, str) or not default.strip():
            raise RulesError("'default' must be a non-empty string")
        raw_rules = data.get("rules", [])
        if not isinstance(raw_rules, list):
            raise RulesError("'rules' must be a list")
        rules = []
        for index, raw in enumerate(raw_rules, 1):
            where = f"rule {index}"
            if not isinstance(raw, dict):
                raise RulesError(f"{where}: must be an object")
            category = raw.get("category")
            if not isinstance(category, str) or not category.strip():
                raise RulesError(f"{where}: missing or empty 'category'")
            has_contains, has_regex = "contains" in raw, "regex" in raw
            if has_contains == has_regex:
                raise RulesError(f"{where}: needs exactly one of 'contains' or 'regex'")
            kind = "contains" if has_contains else "regex"
            pattern = raw[kind]
            if not isinstance(pattern, str) or not pattern:
                raise RulesError(f"{where}: '{kind}' must be a non-empty string")
            priority = raw.get("priority", 0)
            if not isinstance(priority, int) or isinstance(priority, bool):
                raise RulesError(f"{where}: 'priority' must be an integer")
            direction = raw.get("direction")
            if direction not in (None, "in", "out"):
                raise RulesError(f"{where}: 'direction' must be 'in' or 'out'")
            rules.append(Rule(category, pattern, kind, priority, direction))
        return cls(rules, default)

    def categorize(self, txn: Transaction) -> str:
        best: Optional[Rule] = None
        for rule in self.rules:
            if rule.matches(txn) and (best is None or rule.priority > best.priority):
                best = rule
        return best.category if best else self.default


def load_rules(path) -> RuleSet:
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise RulesError(f"cannot read rules file {path}: {exc}") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RulesError(f"invalid JSON in rules file {path}: {exc}") from None
    return RuleSet.from_dict(data)


def categorize_ledger(ledger, ruleset: RuleSet, recategorize: bool = False) -> int:
    count = 0
    for txn in ledger.transactions():
        if txn.category is not None and not recategorize:
            continue
        ledger.set_category(txn.id, ruleset.categorize(txn))
        count += 1
    return count
