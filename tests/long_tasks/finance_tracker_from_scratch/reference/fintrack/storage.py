"""SQLite-backed ledger with duplicate detection and budgets.

Money is stored as integer cents so it round-trips exactly.
"""
from __future__ import annotations

import datetime
import sqlite3
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Iterable, List, Optional

from fintrack.errors import ValidationError
from fintrack.models import Transaction, quantize, to_money

UNCATEGORIZED = "Uncategorized"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    description TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    account     TEXT NOT NULL,
    category    TEXT,
    fingerprint TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_txn_fingerprint ON transactions(fingerprint);
CREATE INDEX IF NOT EXISTS ix_txn_date ON transactions(date);
CREATE TABLE IF NOT EXISTS budgets (
    category     TEXT PRIMARY KEY,
    amount_cents INTEGER NOT NULL
);
"""


@dataclass
class AddResult:
    added: int
    duplicates: int


def _cents(amount: Decimal) -> int:
    return int(quantize(amount) * 100)


def _from_cents(cents: int) -> Decimal:
    return quantize(Decimal(cents) / 100)


def _fingerprint(txn: Transaction) -> str:
    return "\x1f".join([txn.date.isoformat(), str(_cents(txn.amount)),
                        txn.description.casefold(), txn.account])


class Ledger:
    def __init__(self, path):
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "Ledger":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ── transactions ──────────────────────────────────────────────────────

    def add_transactions(self, txns: Iterable[Transaction]) -> AddResult:
        batch = list(txns)
        wanted = Counter(_fingerprint(t) for t in batch)
        existing = {}
        for fp in wanted:
            (existing[fp],) = self._conn.execute(
                "SELECT COUNT(*) FROM transactions WHERE fingerprint = ?", (fp,)).fetchone()
        allowance = {fp: max(0, k - existing[fp]) for fp, k in wanted.items()}
        added = 0
        with self._conn:
            for txn in batch:
                fp = _fingerprint(txn)
                if allowance[fp] <= 0:
                    continue
                allowance[fp] -= 1
                self._conn.execute(
                    "INSERT INTO transactions (date, description, amount_cents, account, category, fingerprint)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (txn.date.isoformat(), txn.description, _cents(txn.amount), txn.account,
                     txn.category, fp))
                added += 1
        return AddResult(added=added, duplicates=len(batch) - added)

    def transactions(self, start=None, end=None, category=None, account=None) -> List[Transaction]:
        sql = "SELECT id, date, description, amount_cents, account, category FROM transactions WHERE 1=1"
        args: list = []
        if start is not None:
            sql += " AND date >= ?"
            args.append(start.isoformat())
        if end is not None:
            sql += " AND date <= ?"
            args.append(end.isoformat())
        if category is not None:
            if category == UNCATEGORIZED:
                sql += " AND (category IS NULL OR category = ?)"
            else:
                sql += " AND category = ?"
            args.append(category)
        if account is not None:
            sql += " AND account = ?"
            args.append(account)
        sql += " ORDER BY date, id"
        return [
            Transaction(datetime.date.fromisoformat(d), desc, _from_cents(c), acct, cat, tid)
            for tid, d, desc, c, acct, cat in self._conn.execute(sql, args)
        ]

    def set_category(self, txn_id: int, category: Optional[str]) -> None:
        with self._conn:
            cur = self._conn.execute("UPDATE transactions SET category = ? WHERE id = ?", (category, txn_id))
        if cur.rowcount == 0:
            raise KeyError(txn_id)

    # ── budgets ───────────────────────────────────────────────────────────

    def set_budget(self, category: str, amount) -> None:
        if not isinstance(category, str) or not category.strip():
            raise ValidationError("budget category must be a non-empty string")
        value = to_money(amount)
        if value <= 0:
            raise ValidationError(f"budget must be greater than 0, got {value}")
        with self._conn:
            self._conn.execute(
                "INSERT INTO budgets (category, amount_cents) VALUES (?, ?)"
                " ON CONFLICT(category) DO UPDATE SET amount_cents = excluded.amount_cents",
                (category, _cents(value)))

    def get_budgets(self) -> Dict[str, Decimal]:
        rows = self._conn.execute("SELECT category, amount_cents FROM budgets ORDER BY category")
        return {cat: _from_cents(c) for cat, c in rows}

    def remove_budget(self, category: str) -> bool:
        with self._conn:
            cur = self._conn.execute("DELETE FROM budgets WHERE category = ?", (category,))
        return cur.rowcount > 0
