"""Order processing — golden fix combines tier + bulk correctly."""

from config import BULK_DISCOUNT_PCT, BULK_MIN_QUANTITY
from discount_calculator import apply_percent_discount, get_customer_tier, get_tier_discount, line_total
from inventory import has_stock
from validation import validate_order


def order_subtotal(order: dict) -> float:
    validate_order(order)
    return round(sum(line_total(i["unit_price"], i["qty"]) for i in order["items"]), 2)


def is_bulk_order(order: dict) -> bool:
    total_qty = sum(item["qty"] for item in order["items"])
    return total_qty >= BULK_MIN_QUANTITY


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
    total = apply_percent_discount(subtotal, BULK_DISCOUNT_PCT)
    return {
        "subtotal": subtotal,
        "total": total,
        "discount_pct": BULK_DISCOUNT_PCT,
        "is_bulk": True,
    }


def process_bulk_order_with_tier(order: dict, lifetime_spend: float) -> dict:
    """Bulk + tier integration — applies both discounts correctly."""
    validate_order(order)
    for item in order["items"]:
        if not has_stock(item["sku"], item["qty"]):
            raise ValueError("out of stock")
    
    subtotal = order_subtotal(order)
    tier = get_customer_tier(lifetime_spend)
    tier_discount = get_tier_discount(tier)
    is_bulk = is_bulk_order(order)
    
    if is_bulk:
        # Combined discount: tier + bulk, not additive
        # Apply tier first, then bulk on the discounted amount
        after_tier = apply_percent_discount(subtotal, tier_discount)
        total = apply_percent_discount(after_tier, BULK_DISCOUNT_PCT)
        combined_pct = round((1 - total / subtotal) * 100, 2)
    else:
        # Only tier discount
        total = apply_percent_discount(subtotal, tier_discount)
        combined_pct = tier_discount
    
    return {
        "subtotal": subtotal,
        "total": total,
        "discount_pct": combined_pct,
        "tier": tier,
        "is_bulk": is_bulk,
    }
