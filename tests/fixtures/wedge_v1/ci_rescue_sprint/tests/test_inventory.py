from inventory import release_stock, reorder_point, reserve_stock, sku_normalize, stock_available


def test_sku_normalize():
    assert sku_normalize("  ab-12  ") == "AB-12"


def test_stock_available():
    assert stock_available(10, 3) == 7


def test_reserve_stock():
    on_hand, reserved = reserve_stock(10, 2, 3)
    assert on_hand == 10
    assert reserved == 5


def test_release_stock():
    on_hand, reserved = release_stock(10, 5, 2)
    assert on_hand == 10
    assert reserved == 3


def test_reorder_point_when_low():
    assert reorder_point(on_hand=10, reserved=2, daily_sales=2, lead_days=5) is True


def test_reorder_point_when_ok():
    assert reorder_point(on_hand=100, reserved=0, daily_sales=1, lead_days=5) is False
