"""Shipping helpers — golden (fixed) reference for ci_rescue_sprint."""


def shipping_cost(weight_kg: float) -> float:
    if weight_kg <= 0:
        raise ValueError("weight must be positive")
    if weight_kg <= 1:
        return 5.0
    if weight_kg <= 5:
        return 12.0
    return 25.0


def qualifies_free_shipping(subtotal: float, threshold: float = 50.0) -> bool:
    return subtotal >= threshold


def delivery_days(distance_km: int, express: bool) -> int:
    base = 1 if distance_km <= 100 else 3 if distance_km <= 500 else 7
    if express:
        return max(1, base - 1)
    return base


def can_ship_international(country: str, blocked: set[str]) -> bool:
    code = country.strip().upper()
    return code not in blocked
