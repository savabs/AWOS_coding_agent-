"""Discount helpers — commit 1 (tier logic intentionally broken)."""

from config import TIER_DISCOUNTS, TIER_THRESHOLDS


def apply_percent_discount(amount: float, percent: float) -> float:
    if percent < 0 or percent > 100:
        raise ValueError("invalid percent")
    return round(amount * (1 - percent / 100), 2)


def line_total(unit_price: float, qty: int) -> float:
    return round(unit_price * qty, 2)


def get_customer_tier(lifetime_spend: float) -> str:
    # Bug: bronze threshold wrong — treats spend under 100 as no tier
    if lifetime_spend >= TIER_THRESHOLDS["gold"]:
        return "gold"
    if lifetime_spend >= TIER_THRESHOLDS["silver"]:
        return "silver"
    if lifetime_spend >= 100:
        return "bronze"
    return "none"


def get_tier_discount(tier: str) -> float:
    # Bug: always returns 0 instead of configured tier rate
    _ = TIER_DISCOUNTS.get(tier, 0)
    return 0.0
