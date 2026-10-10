"""CSV export of orders for accounting / spreadsheets.

The export format is deliberately flat - one row per order - and stable:
downstream spreadsheets depend on the column order below.
"""

import csv
import os
import tempfile

from .errors import StorageError, ValidationError
from .models import OrderStatus
from .reports import customer_totals
from .utils.dates import format_timestamp
from .utils.money import format_money

EXPORT_COLUMNS = ["id", "created_at", "customer", "email", "status", "items", "total"]


def write_output_file(path, write):
    """Call ``write(stream)`` on a temporary file and move it over ``path``.

    The target is replaced atomically, so a failure part-way through leaves any
    existing file untouched and no temporary file behind. Problems are raised
    as :class:`StorageError` naming the path. Returns what ``write`` returns.
    """
    directory = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(directory):
        raise StorageError(f"cannot write {path}: directory {directory} does not exist")
    if os.path.isdir(path):
        raise StorageError(f"cannot write {path}: it is a directory")
    try:
        fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
    except OSError as exc:
        raise StorageError(f"cannot write {path}: {exc.strerror or exc}") from None
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
            result = write(fh)
        os.replace(tmp_path, path)
    except OSError as exc:
        raise StorageError(f"cannot write {path}: {exc.strerror or exc}") from None
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
    return result


def order_to_row(order):
    """Flatten one order into the export row layout."""
    return [
        order.id,
        format_timestamp(order.created_at),
        order.customer,
        order.email,
        order.status.value,
        order.item_count,
        format_money(order.total),
    ]


def filter_orders(orders, date_from=None, date_to=None, status=None):
    """Return the orders matching all given filters.

    ``date_from`` / ``date_to`` are :class:`datetime.date` values and are
    inclusive: an order placed at any time on ``date_to`` is included.
    ``status`` is an :class:`OrderStatus` or status name.
    """
    wanted = OrderStatus.parse(status) if status is not None else None
    if date_from and date_to and date_from > date_to:
        raise ValidationError(
            f"--from date {date_from.isoformat()} is after --to date {date_to.isoformat()}"
        )
    selected = []
    for order in orders:
        day = order.created_at.date()
        if date_from is not None and day < date_from:
            continue
        if date_to is not None and day > date_to:
            continue
        if wanted is not None and order.status != wanted:
            continue
        selected.append(order)
    return selected


def export_orders_csv(orders, stream, date_from=None, date_to=None, status=None):
    """Write ``orders`` as CSV (with a header row) to a text ``stream``.

    Optional ``date_from``/``date_to``/``status`` filters are applied via
    :func:`filter_orders`. Returns the number of order rows written.
    """
    orders = filter_orders(orders, date_from=date_from, date_to=date_to, status=status)
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(EXPORT_COLUMNS)
    count = 0
    for order in orders:
        writer.writerow(order_to_row(order))
        count += 1
    return count


def export_line_items_csv(orders, stream):
    """Write one row per line item (order id, sku, quantity, unit price, subtotal).

    Returns the number of line-item rows written.
    """
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(["order_id", "sku", "quantity", "unit_price", "subtotal"])
    count = 0
    for order in orders:
        for item in order.items:
            writer.writerow(
                [
                    order.id,
                    item.sku,
                    item.quantity,
                    format_money(item.unit_price),
                    format_money(item.subtotal),
                ]
            )
            count += 1
    return count


CUSTOMER_COLUMNS = ["customer", "email", "orders", "total_spent"]


def export_customers_csv(orders, stream):
    """Write one row per customer (see :func:`reports.customer_totals`).

    Returns the number of customer rows written.
    """
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(CUSTOMER_COLUMNS)
    count = 0
    for name, email, order_count, total in customer_totals(orders):
        writer.writerow([name, email, order_count, format_money(total)])
        count += 1
    return count
