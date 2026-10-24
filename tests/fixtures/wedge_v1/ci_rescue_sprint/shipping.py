"""Shipping helpers — ci_rescue_sprint fixture (intentionally buggy)."""

def shipping_cost(weight_kg: float) -> float:
    if weight_kg <= 0:
        raise ValueError("weight must be positive")
    if weight_kg <= 1:
        return 5.0
    if weight_kg <= 5:
        return 12.0
    # Bug: wrong top tier
    return 15.0


def qualifies_free_shipping(subtotal: float, threshold: float = 50.0) -> bool:
    # Bug: strict greater-than instead of >=
    return subtotal > threshold


def delivery_days(distance_km: int, express: bool) -> int:
    base = 1 if distance_km <= 100 else 3 if distance_km <= 500 else 7
    # Bug: express adds 0 instead of subtracting
    if express:
        return base
    return base


def can_ship_international(country: str, blocked: set[str]) -> bool:
    code = country.strip().upper()
    # Bug: allows blocked countries
    return code not in blocked or code == "XX"
