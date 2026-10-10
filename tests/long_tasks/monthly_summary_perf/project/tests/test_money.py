from decimal import Decimal

import pytest

from ledger.money import average, convert, format_money, fx_fee, parse_amount, parse_rate


@pytest.mark.parametrize("text,expected", [
    ("12.30", "12.30"),
    ("-4", "-4.00"),
    ("1,234.50", "1234.50"),
    ("(12.00)", "-12.00"),
    (" 0.125 ", "0.12"),  # banker's rounding
    ("0.135", "0.14"),
])
def test_parse_amount(text, expected):
    assert parse_amount(text) == Decimal(expected)
    assert str(parse_amount(text)) == expected


def test_parse_amount_rejects_empty():
    with pytest.raises(ValueError):
        parse_amount("  ")


def test_convert_uses_two_step_rounding():
    # 1.234950 -> 1.2350 (HALF_UP at 4dp) -> 1.24 (HALF_EVEN at 2dp).
    # A single HALF_EVEN rounding straight to cents would give 1.23.
    assert convert(Decimal("1.00"), Decimal("1.234950")) == Decimal("1.24")
    assert convert(Decimal("1.00"), Decimal("1.224950")) == Decimal("1.22")
    assert convert(Decimal("-1.00"), Decimal("1.234950")) == Decimal("-1.24")
    assert convert(Decimal("100.00"), Decimal("0.856123")) == Decimal("85.61")


def test_fx_fee_rounds_half_up_per_transaction():
    assert fx_fee(Decimal("-10.00")) == Decimal("0.28")  # 0.275 -> 0.28
    assert fx_fee(Decimal("18.00")) == Decimal("0.50")  # 0.495 -> 0.50


def test_average():
    assert average(Decimal("10.00"), 4) == Decimal("2.50")
    assert average(Decimal("0.05"), 2) == Decimal("0.02")  # 0.025 -> 0.02 (even)
    assert average(Decimal("5.00"), 0) == Decimal("0.00")


def test_parse_rate_and_format():
    assert str(parse_rate("0.8561234")) == "0.856123"
    assert format_money(Decimal("-1234.5"), "GBP") == "-£1,234.50"
    assert format_money(Decimal("3"), "XYZ") == "XYZ 3.00"
