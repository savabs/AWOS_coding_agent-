"""Discount helpers — commit 0 baseline (all correct)."""


def apply_percent_discount(amount: float, percent: float) -> float:
    if percent < 0 or percent > 100:
        raise ValueError("invalid percent")
    return round(amount * (1 - percent / 100), 2)


def line_total(unit_price: float, qty: int) -> float:
    return round(unit_price * qty, 2)
