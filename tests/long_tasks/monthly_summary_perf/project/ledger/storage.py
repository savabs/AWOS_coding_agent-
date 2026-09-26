"""In-memory ledger storage.

The store keeps ledger rows exactly as they arrive from the bank export
(dicts of strings), plus the account master data.  Rows may arrive out of
order (late postings); readers always get transactions in posting order:
by posting date, then by transaction id.
"""

import csv

from .models import ROW_FIELDS, Account, Transaction


class UnknownAccountError(KeyError):
    pass


class LedgerStore:
    def __init__(self):
        self._rows = []
        self._accounts = {}

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
        self._rows.append({f: str(row[f]) for f in ROW_FIELDS})

    def add_transaction(self, txn):
        self.add_row(txn.to_row())

    def __len__(self):
        return len(self._rows)

    def get(self, txn_id):
        for row in self._rows:
            if row["id"].strip() == txn_id:
                return Transaction.from_row(row)
        raise KeyError(txn_id)

    def transactions(self, account_id, start=None, end=None):
        """Transactions of ``account_id`` posted within [start, end] (inclusive).

        ``start`` / ``end`` are ``datetime.date`` or ``None`` for open ranges.
        """
        result = []
        for row in self._rows:
            txn = Transaction.from_row(row)
            if txn.account_id != account_id:
                continue
            if start is not None and txn.posted_on < start:
                continue
            if end is not None and txn.posted_on > end:
                continue
            result.append(txn)
        result.sort(key=lambda t: (t.posted_on, t.txn_id))
        return result

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
