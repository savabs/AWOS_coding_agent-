"""Promo helpers — ci_rescue_sprint fixture (intentionally buggy)."""

def stackable(base_percent: float, extra_percent: float) -> float:
    if base_percent < 0 or extra_percent < 0:
        raise ValueError("percents must be non-negative")
    # Bug: adds percents linearly without cap
    return min(base_percent + extra_percent, 80.0)


def min_order_for_promo(subtotal: float, minimum: float) -> bool:
    # Bug: wrong comparison
    return subtotal < minimum


def loyalty_points(spend: float, multiplier: float = 1.0) -> int:
    # Bug: floors instead of rounds
    return int(spend * multiplier)


def bundle_discount(item_count: int) -> float:
    if item_count >= 5:
        return 0.15
    if item_count >= 3:
        return 0.10
    # Bug: gives discount for single item
    return 0.05
