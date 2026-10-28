"""Promo helpers — golden (fixed) reference for ci_rescue_sprint."""


def stackable(base_percent: float, extra_percent: float) -> float:
    if base_percent < 0 or extra_percent < 0:
        raise ValueError("percents must be non-negative")
    combined = 1 - (1 - base_percent / 100) * (1 - extra_percent / 100)
    return combined * 100


def min_order_for_promo(subtotal: float, minimum: float) -> bool:
    return subtotal >= minimum


def loyalty_points(spend: float, multiplier: float = 1.0) -> int:
    return round(spend * multiplier)


def bundle_discount(item_count: int) -> float:
    if item_count >= 5:
        return 0.15
    if item_count >= 3:
        return 0.10
    return 0.0
