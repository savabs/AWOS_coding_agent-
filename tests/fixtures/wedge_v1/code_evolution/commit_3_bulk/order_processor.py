"""Order processing — commit 3 adds bulk order handling (intentionally broken)."""

from config import BULK_DISCOUNT_PCT, BULK_MIN_QUANTITY
from discount_calculator import apply_percent_discount, get_customer_tier, get_tier_discount, line_total
from inventory import has_stock
from validation import validate_order


def order_subtotal(order: dict) -> float:
    validate_order(order)
    return round(sum(line_total(i["unit_price"], i["qty"]) for i in order["items"]), 2)


def is_bulk_order(order: dict) -> bool:
    # Bug: doesn't count total quantity across items
    for item in order["items"]:
        if item["qty"] >= BULK_MIN_QUANTITY:
            return True
    return False


def process_order(order: dict, discount_pct: float = 0.0) -> dict:
    validate_order(order)
    for item in order["items"]:
        if not has_stock(item["sku"], item["qty"]):
            raise ValueError("out of stock")
    subtotal = order_subtotal(order)
    total = apply_percent_discount(subtotal, discount_pct)
    return {
        "subtotal": subtotal,
        "total": total,
        "discount_pct": discount_pct,
    }


def process_order_with_tier(order: dict, lifetime_spend: float) -> dict:
    validate_order(order)
    for item in order["items"]:
        if not has_stock(item["sku"], item["qty"]):
            raise ValueError("out of stock")
    subtotal = order_subtotal(order)
    tier = get_customer_tier(lifetime_spend)
    discount_pct = get_tier_discount(tier)
    total = apply_percent_discount(subtotal, discount_pct)
    return {
        "subtotal": subtotal,
        "total": total,
        "discount_pct": discount_pct,
        "tier": tier,
    }


def process_bulk_order(order: dict) -> dict:
    validate_order(order)
    for item in order["items"]:
        if not has_stock(item["sku"], item["qty"]):
            raise ValueError("out of stock")
    
    if not is_bulk_order(order):
        raise ValueError("not a bulk order")
    
    subtotal = order_subtotal(order)
    # Bug: doesn't properly apply bulk discount
    total = subtotal
    return {
        "subtotal": subtotal,
        "total": total,
        "discount_pct": 0.0,
        "is_bulk": True,
    }
