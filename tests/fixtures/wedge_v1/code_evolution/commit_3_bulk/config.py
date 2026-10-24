"""Shop settings — commit 3 adds bulk order thresholds."""

ORDER_MIN_TOTAL = 1.0
DEFAULT_DISCOUNT_PCT = 0.0

TIER_THRESHOLDS = {
    "bronze": 0,
    "silver": 500,
    "gold": 2000,
}

TIER_DISCOUNTS = {
    "bronze": 5,
    "silver": 10,
    "gold": 15,
}

BULK_MIN_QUANTITY = 10
BULK_DISCOUNT_PCT = 5
