from inventory import has_stock


def test_has_stock_when_available():
    assert has_stock("SKU-A", 3) is True


def test_has_stock_when_unavailable():
    assert has_stock("SKU-C", 1) is False
