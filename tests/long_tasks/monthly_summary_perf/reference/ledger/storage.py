"""In-memory ledger storage.

The store keeps ledger rows exactly as they arrive from the bank export
(dicts of strings), plus the account master data.  Rows may arrive out of
order (late postings); readers always get transactions in posting order:
by posting date, then by transaction id.

Rows are parsed once and indexed per account (sorted by posting order); the
index is rebuilt lazily after new rows are added.
"""

import csv
from bisect import bisect_left, bisect_right

from .models import ROW_FIELDS, Account, Transaction


class UnknownAccountError(KeyError):
    pass


class LedgerStore:
    def __init__(self):
        self._rows = []
        self._accounts = {}
        self._parsed = []  # Transaction for each row in self._rows, same order
        self._index = None  # account_id -> (sorted txns, sorted dates)

    # -- accounts ---------------------------------------------------------
    def add_account(self, account):
        if not isinstance(account, Account):
            raise TypeError("expected an Account")
        self._accounts[account.account_id] = account

    def account(self, account_id):
        try:
            return self._accounts[account_id]
        except KeyError:
            raise UnknownAccountError(account_id) from None

    def accounts(self):
        return sorted(self._accounts.values(), key=lambda a: a.account_id)

    # -- rows ---------------------------------------------------------------
    def add_row(self, row):
        missing = [f for f in ROW_FIELDS if f not in row]
        if missing:
            raise ValueError(f"row is missing fields: {', '.join(missing)}")
        clean = {f: str(row[f]) for f in ROW_FIELDS}
        txn = Transaction.from_row(clean)  # validates the row up front
        self._rows.append(clean)
        self._parsed.append(txn)
        self._index = None

    def add_transaction(self, txn):
        self.add_row(txn.to_row())

    def __len__(self):
        return len(self._rows)

    def get(self, txn_id):
        for txn in self._parsed:
            if txn.txn_id == txn_id:
                return txn
        raise KeyError(txn_id)

    def _account_index(self, account_id):
        if self._index is None:
            grouped = {}
            for txn in self._parsed:
                grouped.setdefault(txn.account_id, []).append(txn)
            index = {}
            for acc, txns in grouped.items():
                txns.sort(key=lambda t: (t.posted_on, t.txn_id))
                index[acc] = (txns, [t.posted_on for t in txns])
            self._index = index
        return self._index.get(account_id, ([], []))

    def transactions(self, account_id, start=None, end=None):
        """Transactions of ``account_id`` posted within [start, end] (inclusive).

        ``start`` / ``end`` are ``datetime.date`` or ``None`` for open ranges.
        """
        txns, dates = self._account_index(account_id)
        lo = 0 if start is None else bisect_left(dates, start)
        hi = len(dates) if end is None else bisect_right(dates, end)
        return txns[lo:hi]

    # -- persistence --------------------------------------------------------
    def load_csv(self, path):
        with open(path, newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                self.add_row(row)

    def save_csv(self, path):
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(ROW_FIELDS))
            writer.writeheader()
            for row in self._rows:
                writer.writerow(row)
