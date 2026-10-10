import os
import sys
from datetime import datetime

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ordertool.services import OrderService  # noqa: E402
from ordertool.storage import OrderStore  # noqa: E402


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "orders.csv")


@pytest.fixture
def service(db_path):
    return OrderService(OrderStore(db_path))


@pytest.fixture
def seeded(service):
    """A service with three orders on different days."""
    service.create_order("Ada Lovelace", "ada@example.com", ["WIDGET:2:9.99"],
                         created_at=datetime(2024, 3, 1, 9, 15))
    service.create_order("Grace Hopper", "grace@example.com", ["GADGET:1:25.00", "CABLE:3:4.50"],
                         created_at=datetime(2024, 3, 5, 17, 40))
    service.create_order("Alan Turing", "alan@example.com", ["WIDGET:1:9.99"],
                         created_at=datetime(2024, 3, 9, 12, 0))
    return service
