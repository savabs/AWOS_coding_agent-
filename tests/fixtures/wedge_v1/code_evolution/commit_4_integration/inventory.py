"""Inventory helpers — commit 0 baseline (all correct)."""

STOCK = {
    "SKU-A": 10,
    "SKU-B": 5,
    "SKU-C": 0,
}


def has_stock(sku: str, quantity: int) -> bool:
    return STOCK.get(sku, 0) >= quantity
