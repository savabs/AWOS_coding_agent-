"""Payment helpers — wedge v1 fixture (intentionally buggy)."""

from decimal import Decimal, ROUND_HALF_UP


def apply_discount(amount: float, percent: float) -> float:
    """Apply percent discount to amount. E.g. 10% off 100 → 90."""
    if percent < 0 or percent > 100:
        raise ValueError("percent must be 0–100")
    return amount - (amount * percent / 100 / 100)


def apply_tax(amount: float, rate: float) -> float:
    """Apply tax rate and round to 2 decimal places (half-up)."""
    taxed = amount * (1 + rate)
    # Bug: truncates instead of half-up quantize
    return int(taxed * 100) / 100


def is_refundable(days_since_purchase: int, opened: bool) -> bool:
    """Refund if within 30 days and package unopened."""
    if opened:
        return True
    return days_since_purchase <= 30
