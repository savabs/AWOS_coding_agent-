"""Tests for payment helpers — 3 failing, 2 passing (regression guard)."""

from payment import apply_discount, apply_tax, is_refundable


def test_discount_ten_percent():
    assert apply_discount(100.0, 10.0) == 90.0


def test_discount_zero():
    assert apply_discount(50.0, 0.0) == 50.0


def test_tax_rounds_half_up():
    assert apply_tax(10.005, 0.075) == 10.76


def test_tax_simple():
    assert apply_tax(100.0, 0.1) == 110.0


def test_refund_unopened_within_window():
    assert is_refundable(10, opened=False) is True


def test_no_refund_opened():
    assert is_refundable(5, opened=True) is False


def test_no_refund_too_old():
    assert is_refundable(45, opened=False) is False
