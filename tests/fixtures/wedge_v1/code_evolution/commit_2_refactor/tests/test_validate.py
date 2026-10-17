from order_processor import validate_order


def test_validate_accepts_valid_order():
    validate_order({"items": [{"sku": "SKU-A", "qty": 1, "unit_price": 10.0}]})


def test_validate_rejects_empty_items():
    try:
        validate_order({"items": []})
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "empty" in str(exc).lower()


def test_validate_rejects_non_positive_qty():
    try:
        validate_order({"items": [{"sku": "SKU-A", "qty": 0, "unit_price": 10.0}]})
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "quantity" in str(exc).lower()


def test_validate_rejects_negative_price():
    try:
        validate_order({"items": [{"sku": "SKU-A", "qty": 1, "unit_price": -1.0}]})
        assert False, "expected ValueError"
    except ValueError:
        pass
