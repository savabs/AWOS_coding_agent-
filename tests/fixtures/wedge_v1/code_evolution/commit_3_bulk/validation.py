"""Order validation — commit 2 refactor (price check accidentally dropped)."""


def validate_order(order: dict) -> None:
    items = order.get("items")
    if not items:
        raise ValueError("empty order")
    for item in items:
        if item["qty"] <= 0:
            raise ValueError("invalid quantity")
        # Bug: refactor dropped negative price guard
