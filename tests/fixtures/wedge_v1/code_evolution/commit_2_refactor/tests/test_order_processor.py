from order_processor import order_subtotal, process_order


def _sample_order():
    return {
        "items": [
            {"sku": "SKU-A", "qty": 2, "unit_price": 25.0},
            {"sku": "SKU-B", "qty": 1, "unit_price": 10.0},
        ]
    }


def test_order_subtotal():
    assert order_subtotal(_sample_order()) == 60.0


def test_process_order_happy_path():
    result = process_order(_sample_order(), discount_pct=10.0)
    assert result["subtotal"] == 60.0
    assert result["total"] == 54.0
    assert result["discount_pct"] == 10.0


def test_process_order_rejects_out_of_stock():
    order = {"items": [{"sku": "SKU-C", "qty": 1, "unit_price": 5.0}]}
    try:
        process_order(order)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "stock" in str(exc).lower()


def test_process_order_rejects_negative_price():
    order = {"items": [{"sku": "SKU-A", "qty": 1, "unit_price": -1.0}]}
    try:
        process_order(order)
        assert False, "expected ValueError"
    except ValueError:
        pass
