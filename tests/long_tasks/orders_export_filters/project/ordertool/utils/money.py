"""Money helpers.

Amounts are handled as :class:`decimal.Decimal` everywhere to avoid float
rounding surprises, and are always rendered with two decimal places.
"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from ..errors import ValidationError

CENT = Decimal("0.01")


def parse_money(value):
    """Parse ``value`` (str, int or Decimal) into a Decimal rounded to cents.

    Raises :class:`ValidationError` for non-numeric or negative input.
    """
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValidationError(f"invalid amount {value!r}") from None
    if not amount.is_finite():
        raise ValidationError(f"invalid amount {value!r}")
    if amount < 0:
        raise ValidationError(f"amount must not be negative: {value!r}")
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def format_money(amount):
    """Render a Decimal amount as a plain string with two decimals, e.g. ``12.50``."""
    return str(Decimal(amount).quantize(CENT, rounding=ROUND_HALF_UP))
