"""CSV export of orders for accounting / spreadsheets.

The export format is deliberately flat - one row per order - and stable:
downstream spreadsheets depend on the column order below.
"""

import csv
import os
import tempfile

from .errors import StorageError, ValidationError
from .models import OrderStatus
from .reports import customer_totals, monthly_totals, product_totals
from .utils.dates import format_timestamp
from .utils.money import format_money, round_money

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


def write_csv(header, rows, stream):
    """Write ``header`` and then every row of ``rows`` as CSV to a text ``stream``.

    This is the one place CSV output is produced, so every file the tool
    writes has the same dialect (``\n`` line endings, minimal quoting).
    ``rows`` may be any iterable of lists. Returns the number of data rows.
    """
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(header)
    count = 0
    for row in rows:
        writer.writerow(row)
        count += 1
    return count


def export_orders_csv(orders, stream, date_from=None, date_to=None, status=None):
    """Write ``orders`` as CSV (with a header row) to a text ``stream``.

    Optional ``date_from``/``date_to``/``status`` filters are applied via
    :func:`filter_orders`. Returns the number of order rows written.
    """
    orders = filter_orders(orders, date_from=date_from, date_to=date_to, status=status)
    return write_csv(EXPORT_COLUMNS, (order_to_row(order) for order in orders), stream)


LINE_ITEM_COLUMNS = ["order_id", "sku", "quantity", "unit_price", "subtotal"]


def export_line_items_csv(orders, stream):
    """Write one row per line item (order id, sku, quantity, unit price, subtotal).

    Returns the number of line-item rows written.
    """
    rows = (
        [
            order.id,
            item.sku,
            item.quantity,
            format_money(item.unit_price),
            format_money(item.subtotal),
        ]
        for order in orders
        for item in order.items
    )
    return write_csv(LINE_ITEM_COLUMNS, rows, stream)


CUSTOMER_COLUMNS = ["customer", "email", "orders", "total_spent"]


def export_customers_csv(orders, stream):
    """Write one row per customer (see :func:`reports.customer_totals`).

    Returns the number of customer rows written.
    """
    rows = (
        [name, email, order_count, format_money(total)]
        for name, email, order_count, total in customer_totals(orders)
    )
    return write_csv(CUSTOMER_COLUMNS, rows, stream)


MONTHLY_COLUMNS = ["month", "orders", "revenue", "average_order"]


def export_monthly_csv(orders, stream):
    """Write one row per calendar month (see :func:`reports.monthly_totals`).

    The average order value is rounded half up to the cent.
    Returns the number of month rows written.
    """
    rows = (
        [month, count, format_money(revenue), format_money(round_money(revenue / count))]
        for month, count, revenue in monthly_totals(orders)
    )
    return write_csv(MONTHLY_COLUMNS, rows, stream)


PRODUCT_COLUMNS = ["sku", "units", "orders", "revenue"]


def export_products_csv(orders, stream):
    """Write one row per SKU (see :func:`reports.product_totals`).

    Returns the number of product rows written.
    """
    rows = (
        [sku, units, order_count, format_money(revenue)]
        for sku, units, order_count, revenue in product_totals(orders)
    )
    return write_csv(PRODUCT_COLUMNS, rows, stream)
