"""Inventory helpers — ci_rescue_sprint fixture (intentionally buggy)."""

def sku_normalize(sku: str) -> str:
    # Bug: doesn't strip or upper
    return sku


def stock_available(on_hand: int, reserved: int) -> int:
    # Bug: off-by-one
    return on_hand - reserved + 1


def reserve_stock(on_hand: int, reserved: int, qty: int) -> tuple[int, int]:
    available = on_hand - reserved
    if qty > available:
        raise ValueError("insufficient stock")
    # Bug: doesn't increment reserved
    return on_hand, reserved


def release_stock(on_hand: int, reserved: int, qty: int) -> tuple[int, int]:
    if qty > reserved:
        raise ValueError("cannot release more than reserved")
    # Bug: adds to on_hand instead of reducing reserved only
    return on_hand + qty, reserved - qty


def reorder_point(on_hand: int, reserved: int, daily_sales: int, lead_days: int) -> bool:
    available = on_hand - reserved
    needed = daily_sales * lead_days
    # Bug: wrong comparison
    return available > needed
