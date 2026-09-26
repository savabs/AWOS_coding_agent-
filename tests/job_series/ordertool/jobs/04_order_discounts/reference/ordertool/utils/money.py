"""Money helpers.

Amounts are handled as :class:`decimal.Decimal` everywhere to avoid float
rounding surprises, and are always rendered with two decimal places.

Rounding rule: amounts are rounded to whole cents *half up* (``0.125`` ->
``0.13``). Use :func:`round_money` for every computed amount; Python's
``round()`` and a bare ``Decimal.quantize`` round half to even instead.
"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from ..errors import ValidationError

CENT = Decimal("0.01")


def round_money(amount):
    """Round a Decimal (or int/str) amount to whole cents, half up."""
    return Decimal(amount).quantize(CENT, rounding=ROUND_HALF_UP)


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
    return round_money(amount)


def format_money(amount):
    """Render a Decimal amount as a plain string with two decimals, e.g. ``12.50``."""
    return str(round_money(amount))


HUNDRED = Decimal("100")


def parse_percent(value):
    """Parse a percentage between 0 and 100 (decimals allowed) into a Decimal.

    Raises :class:`ValidationError` for non-numeric or out-of-range input.
    """
    try:
        pct = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValidationError(f"invalid percentage {value!r}") from None
    if not pct.is_finite() or pct < 0 or pct > HUNDRED:
        raise ValidationError(f"percentage must be between 0 and 100: {value!r}")
    return pct
