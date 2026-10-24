from promo import bundle_discount, loyalty_points, min_order_for_promo, stackable


def test_stackable_compound():
    assert abs(stackable(20.0, 10.0) - 28.0) < 0.01


def test_min_order_for_promo_met():
    assert min_order_for_promo(50.0, 40.0) is True


def test_min_order_for_promo_not_met():
    assert min_order_for_promo(30.0, 40.0) is False


def test_loyalty_points_rounds():
    assert loyalty_points(10.6) == 11


def test_bundle_discount_none_for_one():
    assert bundle_discount(1) == 0.0


def test_bundle_discount_bulk():
    assert bundle_discount(5) == 0.15
