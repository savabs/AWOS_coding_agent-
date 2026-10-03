"""CSV-backed persistence for orders.

All orders live in a single CSV file. Line items are encoded into one
column as ``SKU:QTY:PRICE`` entries separated by ``|``. Writes go through a
temporary file and an atomic rename so a crash never leaves a half-written
database behind.

Files are read with or without a UTF-8 byte-order mark (Excel adds one) and
always written without it.
"""

import csv
import os
import tempfile

from .errors import NotFoundError, StorageError, ValidationError
from .models import LineItem, Order, OrderStatus
from .utils.dates import format_timestamp, parse_timestamp
from .utils.money import format_money

FIELDNAMES = ["id", "customer", "email", "status", "created_at", "items", "discount_pct"]
# Files written before discounts existed lack the last column; they still load.
LEGACY_FIELDNAMES = FIELDNAMES[:-1]
ID_PREFIX = "ORD-"


def encode_items(items):
    """Encode line items into the single-column storage form."""
    return "|".join(
        f"{item.sku}:{item.quantity}:{format_money(item.unit_price)}" for item in items
    )


def decode_items(text):
    """Decode the storage form produced by :func:`encode_items`."""
    if not text:
        return []
    return [LineItem.from_spec(part) for part in text.split("|")]


def order_to_record(order):
    """Convert an Order into a dict suitable for ``csv.DictWriter``."""
    return {
        "id": order.id,
        "customer": order.customer,
        "email": order.email,
        "status": order.status.value,
        "created_at": format_timestamp(order.created_at),
        "items": encode_items(order.items),
        "discount_pct": str(order.discount_pct),
    }


def record_to_order(record):
    """Convert a CSV row dict back into an Order."""
    try:
        return Order(
            id=record["id"],
            customer=record["customer"],
            email=record["email"],
            status=OrderStatus.parse(record["status"]),
            created_at=parse_timestamp(record["created_at"]),
            items=decode_items(record["items"]),
            discount_pct=record.get("discount_pct") or "0",
        )
    except (KeyError, ValidationError) as exc:
        raise StorageError(f"corrupt order record {record.get('id', '?')!r}: {exc}") from None


class OrderStore:
    """Load and save orders from a CSV file at ``path``.

    A missing file is treated as an empty store; it is created on first save.
    """

    def __init__(self, path):
        self.path = str(path)

    # -- reading -------------------------------------------------------

    def load_all(self):
        """Return every order in file order (which is insertion order)."""
        if not os.path.exists(self.path):
            return []
        try:
            # utf-8-sig: files saved by Excel start with a byte-order mark.
            with open(self.path, newline="", encoding="utf-8-sig") as fh:
                reader = csv.DictReader(fh)
                if reader.fieldnames and reader.fieldnames not in (FIELDNAMES, LEGACY_FIELDNAMES):
                    raise StorageError(
                        f"{self.path}: unexpected columns {reader.fieldnames}"
                    )
                return [record_to_order(row) for row in reader]
        except OSError as exc:
            raise StorageError(f"cannot read {self.path}: {exc}") from None

    def get(self, order_id):
        """Return the order with ``order_id`` or raise :class:`NotFoundError`."""
        for order in self.load_all():
            if order.id == order_id:
                return order
        raise NotFoundError(f"no order with id {order_id!r}")

    def next_id(self):
        """Return the next free sequential id, e.g. ``ORD-0007``."""
        highest = 0
        for order in self.load_all():
            if order.id.startswith(ID_PREFIX):
                try:
                    highest = max(highest, int(order.id[len(ID_PREFIX):]))
                except ValueError:
                    continue
        return f"{ID_PREFIX}{highest + 1:04d}"

    # -- writing -------------------------------------------------------

    def save_all(self, orders):
        """Replace the whole file with ``orders`` atomically."""
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
                writer.writeheader()
                for order in orders:
                    writer.writerow(order_to_record(order))
            os.replace(tmp_path, self.path)
        except BaseException:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def add(self, order):
        """Append a new order. Raises StorageError on a duplicate id."""
        orders = self.load_all()
        if any(existing.id == order.id for existing in orders):
            raise StorageError(f"duplicate order id {order.id!r}")
        orders.append(order)
        self.save_all(orders)
        return order

    def update(self, order):
        """Replace the stored order that has the same id."""
        orders = self.load_all()
        for index, existing in enumerate(orders):
            if existing.id == order.id:
                orders[index] = order
                self.save_all(orders)
                return order
        raise NotFoundError(f"no order with id {order.id!r}")
