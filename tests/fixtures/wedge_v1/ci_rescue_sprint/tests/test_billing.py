from billing import apply_coupon, currency_split, invoice_total, line_total, round_money


def test_line_total_multiplies_qty():
    assert line_total(10.0, 3) == 30.0


def test_apply_coupon_ten_percent():
    assert apply_coupon(100.0, 10.0) == 90.0


def test_invoice_total_includes_tax():
    assert invoice_total(100.0, 0.1) == 110.0


def test_round_money_half_up():
    assert round_money(10.005) == 10.01


def test_round_money_simple():
    assert round_money(1.234) == 1.23


def test_currency_split_preserves_total():
    parts = currency_split(100.0, 3)
    assert len(parts) == 3
    assert abs(sum(parts) - 100.0) < 0.01
    assert parts[-1] >= parts[0]
