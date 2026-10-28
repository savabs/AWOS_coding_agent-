from order_processor import is_bulk_order, process_bulk_order


def test_is_bulk_when_total_quantity_exceeds_threshold():
    order = {"items": [
        {"sku": "SKU-A", "qty": 6, "unit_price": 10.0},
        {"sku": "SKU-B", "qty": 5, "unit_price": 10.0},
    ]}
    assert is_bulk_order(order) is True


def test_is_not_bulk_when_under_threshold():
    order = {"items": [{"sku": "SKU-A", "qty": 3, "unit_price": 10.0}]}
    assert is_bulk_order(order) is False


def test_process_bulk_order_applies_discount():
    order = {"items": [
        {"sku": "SKU-A", "qty": 6, "unit_price": 10.0},
        {"sku": "SKU-B", "qty": 5, "unit_price": 10.0},
    ]}
    result = process_bulk_order(order)
    assert result["subtotal"] == 110.0
    assert result["total"] == 104.5
    assert result["discount_pct"] == 5.0
    assert result["is_bulk"] is True
