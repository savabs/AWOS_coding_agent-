from billing import apply_coupon, invoice_total, line_total
from inventory import reserve_stock, stock_available
from shipping import qualifies_free_shipping, shipping_cost


def test_checkout_subtotal():
    subtotal = line_total(25.0, 2)
    assert subtotal == 50.0


def test_checkout_with_coupon_and_tax():
    subtotal = 100.0
    after_coupon = apply_coupon(subtotal, 10.0)
    total = invoice_total(after_coupon, 0.1)
    assert abs(total - 99.0) < 0.01


def test_checkout_stock_reservation():
    assert stock_available(20, 5) == 15
    on_hand, reserved = reserve_stock(20, 5, 4)
    assert stock_available(on_hand, reserved) == 11


def test_checkout_free_shipping_eligible():
    subtotal = 55.0
    assert qualifies_free_shipping(subtotal) is True
    assert shipping_cost(2.0) == 12.0
