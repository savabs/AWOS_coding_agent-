"""Domain model: orders, line items and the order status lifecycle."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum

from .errors import ValidationError
from .utils.money import HUNDRED, parse_money, parse_percent, round_money


class OrderStatus(str, Enum):
    """Lifecycle states of an order."""

    PENDING = "pending"
    PAID = "paid"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"

    @classmethod
    def parse(cls, value):
        """Parse a status name case-insensitively.

        Raises :class:`ValidationError` listing the valid choices when the
        value is not a known status.
        """
        if isinstance(value, cls):
            return value
        normalized = str(value or "").strip().lower()
        for status in cls:
            if status.value == normalized:
                return status
        choices = ", ".join(s.value for s in cls)
        raise ValidationError(f"unknown status {value!r} (choose from: {choices})")


# Which status changes are allowed from each state.
ALLOWED_TRANSITIONS = {
    OrderStatus.PENDING: {OrderStatus.PAID, OrderStatus.CANCELLED},
    OrderStatus.PAID: {OrderStatus.SHIPPED, OrderStatus.CANCELLED},
    OrderStatus.SHIPPED: {OrderStatus.DELIVERED},
    OrderStatus.DELIVERED: set(),
    OrderStatus.CANCELLED: set(),
}


@dataclass
class LineItem:
    """A single product line on an order."""

    sku: str
    quantity: int
    unit_price: Decimal

    def __post_init__(self):
        self.sku = self.sku.strip().upper()
        if not self.sku:
            raise ValidationError("line item SKU must not be empty")
        if int(self.quantity) <= 0:
            raise ValidationError(f"quantity must be positive for {self.sku}")
        self.quantity = int(self.quantity)
        self.unit_price = parse_money(self.unit_price)

    @property
    def subtotal(self):
        """Quantity times unit price."""
        return self.unit_price * self.quantity

    @classmethod
    def from_spec(cls, spec):
        """Build a line item from a ``SKU:QTY:PRICE`` string (as used on the CLI)."""
        parts = spec.split(":")
        if len(parts) != 3:
            raise ValidationError(f"invalid item {spec!r}: expected SKU:QTY:PRICE")
        sku, qty, price = parts
        try:
            quantity = int(qty)
        except ValueError:
            raise ValidationError(f"invalid quantity in item {spec!r}") from None
        return cls(sku=sku, quantity=quantity, unit_price=price)


@dataclass
class Order:
    """A customer order."""

    id: str
    customer: str
    email: str
    created_at: datetime
    status: OrderStatus = OrderStatus.PENDING
    items: list = field(default_factory=list)
    discount_pct: Decimal = Decimal("0")

    def __post_init__(self):
        self.discount_pct = parse_percent(self.discount_pct)

    @property
    def gross_total(self):
        """Sum of all line item subtotals, before any discount."""
        return sum((item.subtotal for item in self.items), Decimal("0.00"))

    @property
    def total(self):
        """The amount due: gross total less the order discount, rounded to cents."""
        if not self.discount_pct:
            return self.gross_total
        return round_money(self.gross_total * (HUNDRED - self.discount_pct) / HUNDRED)

    @property
    def item_count(self):
        """Total number of units across all line items."""
        return sum(item.quantity for item in self.items)

    def can_transition_to(self, new_status):
        """Return True if moving to ``new_status`` is allowed."""
        return new_status in ALLOWED_TRANSITIONS[self.status]
