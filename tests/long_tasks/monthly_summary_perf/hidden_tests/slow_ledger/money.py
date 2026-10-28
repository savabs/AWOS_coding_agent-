"""Money helpers.

All monetary values are ``decimal.Decimal``. Never use floats for money.

Rounding policy (agreed with finance, see FIN-212):
  * stored amounts are held to the cent, banker's rounding (ROUND_HALF_EVEN);
  * currency conversion multiplies by the published rate, rounds to four
    decimal places HALF_UP, and only then rounds to cents HALF_EVEN.  This
    two-step rounding mirrors the card processor's settlement files and must
    be kept exactly, otherwise balances drift from bank statements by a cent
    here and there;
  * foreign-transaction fees are rounded HALF_UP to the cent, per transaction.
"""

from decimal import Decimal, ROUND_HALF_EVEN, ROUND_HALF_UP

CENT = Decimal("0.01")
SETTLEMENT_PLACES = Decimal("0.0001")
RATE_PLACES = Decimal("0.000001")
ZERO = Decimal("0.00")
FX_FEE_RATE = Decimal("0.0275")

_SYMBOLS = {"GBP": "£", "EUR": "€", "USD": "$", "CHF": "CHF ", "SEK": "kr "}


def parse_amount(text):
    """Parse an amount as written in ledger exports.

    Accepts thousands separators and accounting-style negatives, e.g.
    ``"1,234.50"`` and ``"(12.00)"``.
    """
    if isinstance(text, Decimal):
        return text.quantize(CENT, rounding=ROUND_HALF_EVEN)
    cleaned = str(text).strip().replace(",", "")
    if not cleaned:
        raise ValueError("empty amount")
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1].strip()
    return Decimal(cleaned).quantize(CENT, rounding=ROUND_HALF_EVEN)


def to_cents(value):
    """Round a Decimal to cents with banker's rounding."""
    return value.quantize(CENT, rounding=ROUND_HALF_EVEN)


def parse_rate(text):
    """Parse an FX rate; rates are published with six decimal places."""
    return Decimal(str(text).strip()).quantize(RATE_PLACES, rounding=ROUND_HALF_EVEN)


def convert(amount, rate):
    """Convert ``amount`` into the base currency using ``rate``.

    Two-step rounding: 4dp HALF_UP, then cents HALF_EVEN (see module doc).
    """
    raw = amount * rate
    settled = raw.quantize(SETTLEMENT_PLACES, rounding=ROUND_HALF_UP)
    return settled.quantize(CENT, rounding=ROUND_HALF_EVEN)


def fx_fee(converted_amount):
    """Foreign transaction fee for one already-converted transaction."""
    return (abs(converted_amount) * FX_FEE_RATE).quantize(CENT, rounding=ROUND_HALF_UP)


def average(total, count):
    """Average to the cent (banker's rounding); zero when there is nothing."""
    if not count:
        return ZERO
    return (total / Decimal(count)).quantize(CENT, rounding=ROUND_HALF_EVEN)


def format_money(value, currency):
    symbol = _SYMBOLS.get(currency, currency + " ")
    sign = "-" if value < 0 else ""
    return f"{sign}{symbol}{abs(value):,.2f}"
