from shipping import can_ship_international, delivery_days, qualifies_free_shipping, shipping_cost


def test_shipping_cost_light():
    assert shipping_cost(0.5) == 5.0


def test_shipping_cost_heavy():
    assert shipping_cost(10.0) == 25.0


def test_free_shipping_at_threshold():
    assert qualifies_free_shipping(50.0) is True


def test_free_shipping_below_threshold():
    assert qualifies_free_shipping(49.99) is False


def test_delivery_express_faster():
    assert delivery_days(200, express=True) < delivery_days(200, express=False)


def test_can_ship_international_blocks():
    assert can_ship_international("XX", blocked={"XX", "YY"}) is False
