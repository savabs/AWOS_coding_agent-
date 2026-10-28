"""Money helpers. Amounts are always integer cents internally."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹", "NZD": "NZ$", "JPY": "¥"}
ZERO_DECIMAL = {"JPY"}


def parse_amount(text: str) -> int:
    """Parse a decimal amount like ``"12.50"`` into cents.

    Half-cent values round half-up (``"0.005"`` -> 1 cent), which matches the
    payment processor's settlement behaviour.
    """
    try:
        value = Decimal(text.strip().replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"invalid amount: {text!r}") from exc
    if value < 0:
        raise ValueError("amount must be non-negative")
    cents = (value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(cents)


def format_cents(cents: int, currency: str = "USD") -> str:
    """Format integer cents for display, e.g. ``1234567 -> "$12,345.67"``."""
    symbol = SYMBOLS.get(currency, currency + " ")
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    if currency in ZERO_DECIMAL:
        return f"{sign}{symbol}{cents // 100:,}"
    whole, frac = divmod(cents, 100)
    return f"{sign}{symbol}{whole:,}.{frac:02d}"


def sum_cents(values) -> int:
    total = 0
    for value in values:
        total += int(value)
    return total
