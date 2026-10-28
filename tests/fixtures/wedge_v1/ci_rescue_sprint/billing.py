"""Billing helpers — ci_rescue_sprint fixture (intentionally buggy)."""

from decimal import Decimal, ROUND_HALF_UP


def line_total(unit_price: float, quantity: int) -> float:
    # Bug: forgets quantity
    return unit_price


def apply_coupon(subtotal: float, percent: float) -> float:
    if percent < 0 or percent > 100:
        raise ValueError("percent must be 0–100")
    # Bug: double division
    return subtotal - (subtotal * percent / 100 / 100)


def invoice_total(subtotal: float, tax_rate: float) -> float:
    # Bug: forgets tax
    return subtotal


def round_money(amount: float) -> float:
    # Bug: truncates instead of half-up
    return int(amount * 100) / 100


def currency_split(total: float, parts: int) -> list[float]:
    if parts < 1:
        raise ValueError("parts must be >= 1")
    each = round_money(total / parts)
    # Bug: loses remainder on last part
    return [each] * parts
