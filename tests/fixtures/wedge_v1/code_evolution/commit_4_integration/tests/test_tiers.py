from discount_calculator import get_customer_tier, get_tier_discount
from order_processor import process_order_with_tier


def test_bronze_tier_at_fifty_spend():
    assert get_customer_tier(50.0) == "bronze"
    assert get_tier_discount("bronze") == 5.0


def test_silver_tier_at_five_hundred_spend():
    assert get_customer_tier(500.0) == "silver"
    assert get_tier_discount("silver") == 10.0


def test_process_order_with_tier_applies_discount():
    order = {"items": [{"sku": "SKU-A", "qty": 2, "unit_price": 50.0}]}
    result = process_order_with_tier(order, lifetime_spend=600.0)
    assert result["tier"] == "silver"
    assert result["subtotal"] == 100.0
    assert result["total"] == 90.0
    assert result["discount_pct"] == 10.0
