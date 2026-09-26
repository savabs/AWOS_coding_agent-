"""Plain records used across the ledger."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from .money import parse_amount

ROW_FIELDS = ("id", "account", "date", "desc", "amount", "currency")
DATE_FORMAT = "%Y-%m-%d"


def parse_date(text):
    """Parse an ISO ``YYYY-MM-DD`` date as found in ledger exports."""
    text = text.strip()
    if len(text) == 10:
        try:
            return date.fromisoformat(text)  # fast path for canonical dates
        except ValueError:
            pass
    return datetime.strptime(text, DATE_FORMAT).date()


def normalise_description(text):
    """Collapse whitespace; bank exports pad descriptions inconsistently."""
    return " ".join(text.split())


@dataclass(frozen=True)
class Account:
    account_id: str
    holder: str
    base_currency: str
    opening_balance: Decimal
    opened_on: date


@dataclass(frozen=True)
class Transaction:
    txn_id: str
    account_id: str
    posted_on: date
    description: str
    amount: Decimal
    currency: str

    @classmethod
    def from_row(cls, row):
        missing = [f for f in ROW_FIELDS if f not in row]
        if missing:
            raise ValueError(f"row is missing fields: {', '.join(missing)}")
        return cls(
            txn_id=row["id"].strip(),
            account_id=row["account"].strip(),
            posted_on=parse_date(row["date"]),
            description=normalise_description(row["desc"]),
            amount=parse_amount(row["amount"]),
            currency=row["currency"].strip().upper(),
        )

    def to_row(self):
        return {
            "id": self.txn_id,
            "account": self.account_id,
            "date": self.posted_on.strftime(DATE_FORMAT),
            "desc": self.description,
            "amount": str(self.amount),
            "currency": self.currency,
        }

    @property
    def is_debit(self):
        return self.amount < 0
