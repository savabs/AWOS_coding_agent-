"""Order validation — golden (price guard restored)."""


def validate_order(order: dict) -> None:
    items = order.get("items")
    if not items:
        raise ValueError("empty order")
    for item in items:
        if item["qty"] <= 0:
            raise ValueError("invalid quantity")
        if item["unit_price"] < 0:
            raise ValueError("invalid price")
