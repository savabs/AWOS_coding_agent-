"""Core data types and money helpers."""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Optional

from fintrack.errors import ValidationError

CENT = Decimal("0.01")


@dataclass
class Transaction:
    date: datetime.date
    description: str
    amount: Decimal
    account: str = "default"
    category: Optional[str] = None
    id: Optional[int] = None

    def fingerprint(self) -> tuple:
        return (self.date, self.amount, self.description.casefold(), self.account)


def quantize(value) -> Decimal:
    """Round to cents; normalise negative zero to zero."""
    result = Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)
    return result + Decimal("0.00") if result == 0 else result


def to_money(value) -> Decimal:
    """Parse a Decimal/int/str into a quantized Decimal or raise ValidationError."""
    if isinstance(value, float):
        value = repr(value)
    try:
        dec = Decimal(str(value).strip()) if not isinstance(value, Decimal) else value
    except (InvalidOperation, ValueError):
        raise ValidationError(f"invalid amount: {value!r}") from None
    if not dec.is_finite():
        raise ValidationError(f"invalid amount: {value!r}")
    return quantize(dec)


def fmt_money(value: Decimal) -> str:
    return f"{quantize(value):.2f}"


def check_month(year: int, month: int) -> None:
    if not isinstance(month, int) or not 1 <= month <= 12:
        raise ValidationError(f"invalid month: {month!r}")
    if not isinstance(year, int) or not 1 <= year <= 9999:
        raise ValidationError(f"invalid year: {year!r}")


def month_bounds(year: int, month: int) -> tuple[datetime.date, datetime.date]:
    check_month(year, month)
    start = datetime.date(year, month, 1)
    nxt = datetime.date(year + (month == 12), month % 12 + 1, 1)
    return start, nxt - datetime.timedelta(days=1)
