"""CSV export of orders for accounting / spreadsheets.

The export format is deliberately flat - one row per order - and stable:
downstream spreadsheets depend on the column order below.
"""

import csv

from .utils.dates import format_timestamp
from .utils.money import format_money

EXPORT_COLUMNS = ["id", "created_at", "customer", "email", "status", "items", "total"]


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


def export_orders_csv(orders, stream):
    """Write ``orders`` as CSV (with a header row) to a text ``stream``.

    Returns the number of order rows written.
    """
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
