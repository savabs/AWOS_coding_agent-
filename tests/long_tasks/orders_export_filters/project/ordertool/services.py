"""Business operations on orders.

The CLI talks to :class:`OrderService`; the service owns validation and the
status lifecycle, and delegates persistence to an :class:`OrderStore`.
"""

import re

from .errors import InvalidTransitionError, ValidationError
from .models import LineItem, Order, OrderStatus
from .utils.dates import now

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class OrderService:
    """High-level order operations."""

    def __init__(self, store):
        self.store = store

    def create_order(self, customer, email, items, created_at=None):
        """Create and persist a new pending order.

        ``items`` may contain :class:`LineItem` objects or ``SKU:QTY:PRICE``
        strings.
        """
        customer = (customer or "").strip()
        if not customer:
            raise ValidationError("customer name must not be empty")
        email = (email or "").strip()
        if not EMAIL_RE.match(email):
            raise ValidationError(f"invalid email address {email!r}")
        line_items = [
            item if isinstance(item, LineItem) else LineItem.from_spec(item)
            for item in items
        ]
        if not line_items:
            raise ValidationError("an order needs at least one item")
        order = Order(
            id=self.store.next_id(),
            customer=customer,
            email=email,
            created_at=created_at or now(),
            items=line_items,
        )
        return self.store.add(order)

    def set_status(self, order_id, new_status):
        """Move an order to ``new_status``, enforcing the lifecycle rules."""
        status = OrderStatus.parse(new_status)
        order = self.store.get(order_id)
        if order.status == status:
            return order
        if not order.can_transition_to(status):
            raise InvalidTransitionError(
                f"cannot change {order.id} from {order.status.value} to {status.value}"
            )
        order.status = status
        return self.store.update(order)

    def list_orders(self, status=None, customer=None):
        """Return orders, optionally filtered by status and/or customer name.

        The customer filter is a case-insensitive substring match.
        """
        orders = self.store.load_all()
        if status is not None:
            wanted = OrderStatus.parse(status)
            orders = [o for o in orders if o.status == wanted]
        if customer:
            needle = customer.strip().lower()
            orders = [o for o in orders if needle in o.customer.lower()]
        return orders

    def get_order(self, order_id):
        """Return a single order by id."""
        return self.store.get(order_id)
