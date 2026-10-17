from decimal import Decimal

import pytest

from ordertool.errors import ValidationError
from ordertool.models import LineItem, OrderStatus


def test_line_item_from_spec():
    item = LineItem.from_spec("widget:3:1.5")
    assert item.sku == "WIDGET"
    assert item.quantity == 3
    assert item.unit_price == Decimal("1.50")
    assert item.subtotal == Decimal("4.50")


@pytest.mark.parametrize("spec", ["WIDGET", "WIDGET:x:1.00", "WIDGET:0:1.00", "WIDGET:1:-2"])
def test_line_item_rejects_bad_specs(spec):
    with pytest.raises(ValidationError):
        LineItem.from_spec(spec)


def test_status_parse_is_case_insensitive():
    assert OrderStatus.parse(" Shipped ") is OrderStatus.SHIPPED


def test_status_parse_lists_choices():
    with pytest.raises(ValidationError) as excinfo:
        OrderStatus.parse("lost")
    assert "pending" in str(excinfo.value)
