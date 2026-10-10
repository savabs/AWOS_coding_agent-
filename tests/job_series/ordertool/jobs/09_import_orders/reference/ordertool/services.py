"""Business operations on orders.

The CLI talks to :class:`OrderService`; the service owns validation and the
status lifecycle, and delegates persistence to an :class:`OrderStore`.
"""

import re

from .errors import InvalidTransitionError, NotFoundError, ValidationError
from .models import LineItem, Order, OrderStatus
from .storage import ID_PREFIX
from .utils.dates import now

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class OrderService:
    """High-level order operations."""

    def __init__(self, store):
        self.store = store

    def create_order(self, customer, email, items, created_at=None, discount_pct=None):
        """Create and persist a new pending order.

        ``items`` may contain :class:`LineItem` objects or ``SKU:QTY:PRICE``
        strings. ``discount_pct`` is an optional percentage (0-100) taken off
        the whole order.
        """
        order = self.build_order(
            self.store.next_id(), customer, email, items,
            created_at=created_at, discount_pct=discount_pct,
        )
        return self.store.add(order)

    def build_order(self, order_id, customer, email, items, created_at=None, discount_pct=None):
        """Validate the inputs and return a new pending :class:`Order` (not saved)."""
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
        return Order(
            id=order_id,
            customer=customer,
            email=email,
            created_at=created_at or now(),
            items=line_items,
            discount_pct=discount_pct if discount_pct is not None else "0",
        )

    def import_orders(self, rows):
        """Add many new pending orders at once - all of them or none.

        ``rows`` is an iterable of ``(line_number, customer, email, created_at,
        items, discount_pct)`` where ``items`` is a list of ``SKU:QTY:PRICE``
        strings / :class:`LineItem` objects and ``created_at`` a datetime.
        Every row is validated like :meth:`create_order` before anything is
        written; the first bad row raises :class:`ValidationError` prefixed
        with ``line N:``. Returns the new orders.
        """
        existing = self.store.load_all()
        next_number = int(self.store.next_id()[len(ID_PREFIX):])
        new_orders = []
        for line, customer, email, created_at, items, discount_pct in rows:
            order_id = f"{ID_PREFIX}{next_number:04d}"
            try:
                order = self.build_order(
                    order_id, customer, email, items,
                    created_at=created_at, discount_pct=discount_pct,
                )
            except ValidationError as exc:
                raise ValidationError(f"line {line}: {exc}") from None
            new_orders.append(order)
            next_number += 1
        self.store.save_all(existing + new_orders)
        return new_orders

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

    def set_status_many(self, order_ids, new_status):
        """Move several orders to ``new_status`` - all of them or none.

        Every order is checked (existence and lifecycle) before anything is
        written; the first problem raises the same error :meth:`set_status`
        would, and the store is left unchanged. Returns the orders in the
        order of ``order_ids``.
        """
        status = OrderStatus.parse(new_status)
        orders = self.store.load_all()
        by_id = {order.id: order for order in orders}
        changed = []
        for order_id in order_ids:
            order = by_id.get(order_id)
            if order is None:
                raise NotFoundError(f"no order with id {order_id!r}")
            if order.status != status and not order.can_transition_to(status):
                raise InvalidTransitionError(
                    f"cannot change {order.id} from {order.status.value} to {status.value}"
                )
            changed.append(order)
        for order in changed:
            order.status = status
        self.store.save_all(orders)
        return changed

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
