from discount_calculator import apply_percent_discount, line_total


def test_line_total():
    assert line_total(12.5, 2) == 25.0


def test_apply_ten_percent_discount():
    assert apply_percent_discount(100.0, 10.0) == 90.0


def test_apply_zero_discount():
    assert apply_percent_discount(50.0, 0.0) == 50.0
