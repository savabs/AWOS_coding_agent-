from datetime import date
from decimal import Decimal

import pytest

from ledger.fx import FxTable, MissingRateError


@pytest.fixture
def fx():
    table = FxTable("GBP")
    table.add_rate(date(2024, 3, 1), "EUR", "0.850000")  # Friday
    table.add_rate(date(2024, 3, 4), "EUR", "0.860000")  # Monday
    table.add_rate("2024-03-01", "USD", "0.790000")
    table.add_rate(date(2024, 3, 4), "EUR", "0.861000")  # correction, arrives later
    return table


def test_weekend_uses_previous_business_day(fx):
    assert fx.rate("EUR", date(2024, 3, 2)) == Decimal("0.850000")
    assert fx.rate("eur", date(2024, 3, 3)) == Decimal("0.850000")


def test_last_received_rate_for_a_day_wins(fx):
    assert fx.rate("EUR", date(2024, 3, 4)) == Decimal("0.861000")
    assert fx.rate("EUR", date(2024, 3, 20)) == Decimal("0.861000")


def test_missing_rate(fx):
    with pytest.raises(MissingRateError):
        fx.rate("EUR", date(2024, 2, 29))
    with pytest.raises(MissingRateError):
        fx.rate("CHF", date(2024, 3, 5))


def test_base_currency_and_conversion(fx):
    assert fx.rate("GBP", date(2000, 1, 1)) == Decimal("1")
    assert fx.to_base(Decimal("10.005"), "GBP", date(2024, 3, 4)) == Decimal("10.00")
    assert fx.to_base(Decimal("-20.00"), "USD", date(2024, 3, 9)) == Decimal("-15.80")
    assert fx.currencies() == ["EUR", "USD"]
    assert len(fx) == 4
