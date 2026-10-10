from datetime import datetime

import pytest

from ordertool.errors import InvalidTransitionError, NotFoundError, ValidationError
from ordertool.models import OrderStatus
from ordertool.storage import OrderStore


def test_roundtrip_preserves_orders(seeded, db_path):
    orders = OrderStore(db_path).load_all()
    assert [o.id for o in orders] == ["ORD-0001", "ORD-0002", "ORD-0003"]
    assert orders[1].created_at == datetime(2024, 3, 5, 17, 40)
    assert str(orders[1].total) == "38.50"


def test_next_id_increments(seeded):
    assert seeded.store.next_id() == "ORD-0004"


def test_status_lifecycle(seeded):
    seeded.set_status("ORD-0001", "paid")
    seeded.set_status("ORD-0001", "shipped")
    assert seeded.get_order("ORD-0001").status is OrderStatus.SHIPPED
    with pytest.raises(InvalidTransitionError):
        seeded.set_status("ORD-0001", "pending")


def test_unknown_order(seeded):
    with pytest.raises(NotFoundError):
        seeded.set_status("ORD-9999", "paid")


def test_list_filters(seeded):
    seeded.set_status("ORD-0002", "cancelled")
    assert [o.id for o in seeded.list_orders(status="cancelled")] == ["ORD-0002"]
    assert [o.id for o in seeded.list_orders(customer="alan")] == ["ORD-0003"]


def test_create_order_validates_email(service):
    with pytest.raises(ValidationError):
        service.create_order("Bob", "not-an-email", ["WIDGET:1:1.00"])
