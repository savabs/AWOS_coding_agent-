"""Inventory helpers — golden (fixed) reference for ci_rescue_sprint."""


def sku_normalize(sku: str) -> str:
    return sku.strip().upper()


def stock_available(on_hand: int, reserved: int) -> int:
    return on_hand - reserved


def reserve_stock(on_hand: int, reserved: int, qty: int) -> tuple[int, int]:
    available = on_hand - reserved
    if qty > available:
        raise ValueError("insufficient stock")
    return on_hand, reserved + qty


def release_stock(on_hand: int, reserved: int, qty: int) -> tuple[int, int]:
    if qty > reserved:
        raise ValueError("cannot release more than reserved")
    return on_hand, reserved - qty


def reorder_point(on_hand: int, reserved: int, daily_sales: int, lead_days: int) -> bool:
    available = on_hand - reserved
    needed = daily_sales * lead_days
    return available <= needed
