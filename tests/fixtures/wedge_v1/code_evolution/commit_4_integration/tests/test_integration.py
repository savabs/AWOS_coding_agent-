from order_processor import process_bulk_order_with_tier


def test_bulk_silver_tier_combines_discounts():
    order = {"items": [
        {"sku": "SKU-A", "qty": 6, "unit_price": 10.0},
        {"sku": "SKU-B", "qty": 5, "unit_price": 10.0},
    ]}
    result = process_bulk_order_with_tier(order, lifetime_spend=600.0)
    assert result["is_bulk"] is True
    assert result["tier"] == "silver"
    assert result["subtotal"] == 110.0
    # Silver = 10%, Bulk = 5%, combined = 14.5% (not 15%)
    assert result["total"] == 94.05
    assert result["discount_pct"] == 14.5


def test_bulk_bronze_tier_combines_discounts():
    order = {"items": [
        {"sku": "SKU-A", "qty": 8, "unit_price": 10.0},
        {"sku": "SKU-B", "qty": 3, "unit_price": 10.0},
    ]}
    result = process_bulk_order_with_tier(order, lifetime_spend=100.0)
    assert result["tier"] == "bronze"
    assert result["subtotal"] == 110.0
    # Bronze = 5%, Bulk = 5%, combined = 9.75%
    assert result["total"] == 99.27
    assert result["discount_pct"] == 9.77


def test_non_bulk_tier_order_does_not_get_bulk_discount():
    order = {"items": [{"sku": "SKU-A", "qty": 3, "unit_price": 50.0}]}
    result = process_bulk_order_with_tier(order, lifetime_spend=600.0)
    assert result["is_bulk"] is False
    assert result["tier"] == "silver"
    assert result["subtotal"] == 150.0
    # Only tier discount, no bulk
    assert result["total"] == 135.0
    assert result["discount_pct"] == 10.0
