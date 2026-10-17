"""Billing helpers — golden (fixed) reference for ci_rescue_sprint."""

from decimal import Decimal, ROUND_HALF_UP


def line_total(unit_price: float, quantity: int) -> float:
    return unit_price * quantity


def apply_coupon(subtotal: float, percent: float) -> float:
    if percent < 0 or percent > 100:
        raise ValueError("percent must be 0–100")
    return subtotal - (subtotal * percent / 100)


def invoice_total(subtotal: float, tax_rate: float) -> float:
    return round_money(subtotal * (1 + tax_rate))


def round_money(amount: float) -> float:
    return float(Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def currency_split(total: float, parts: int) -> list[float]:
    if parts < 1:
        raise ValueError("parts must be >= 1")
    each = round_money(total / parts)
    result = [each] * (parts - 1)
    result.append(round_money(total - each * (parts - 1)))
    return result
