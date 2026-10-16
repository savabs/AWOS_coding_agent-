"""Order processing — commit 0 baseline (all correct)."""

from discount_calculator import apply_percent_discount, line_total
from inventory import has_stock


def validate_order(order: dict) -> None:
    items = order.get("items")
    if not items:
        raise ValueError("empty order")
    for item in items:
        if item["qty"] <= 0:
            raise ValueError("invalid quantity")
        if item["unit_price"] < 0:
            raise ValueError("invalid price")


def order_subtotal(order: dict) -> float:
    validate_order(order)
    return round(sum(line_total(i["unit_price"], i["qty"]) for i in order["items"]), 2)


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
