"""Command-line interface for ordertool.

Usage::

    python -m ordertool [--db PATH] <command> [options]

The database path defaults to ``$ORDERTOOL_DB`` or ``orders.csv`` in the
current directory. User errors are reported as ``error: <message>`` on
stderr with exit status 1; they never produce a traceback.
"""

import argparse
import os
import sys

from . import __version__
from .errors import OrderToolError
from .export import (
    export_customers_csv,
    export_line_items_csv,
    export_orders_csv,
    filter_orders,
    write_output_file,
)
from .models import OrderStatus
from .reports import render_summary
from .services import OrderService
from .storage import OrderStore
from .utils.dates import format_timestamp, parse_date_arg, parse_timestamp
from .utils.money import format_money

DEFAULT_DB = "orders.csv"


def build_parser():
    """Create the argument parser with all sub-commands."""
    parser = argparse.ArgumentParser(prog="ordertool", description="Manage customer orders.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--db",
        default=os.environ.get("ORDERTOOL_DB", DEFAULT_DB),
        help="path to the orders CSV database (default: $ORDERTOOL_DB or orders.csv)",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    add = sub.add_parser("add", help="create a new order")
    add.add_argument("--customer", required=True)
    add.add_argument("--email", required=True)
    add.add_argument(
        "--item",
        dest="items",
        action="append",
        default=[],
        metavar="SKU:QTY:PRICE",
        help="line item; repeat for several items",
    )
    add.add_argument(
        "--created-at",
        help="order timestamp (YYYY-MM-DDTHH:MM:SS); defaults to now",
    )
    add.add_argument(
        "--discount",
        metavar="PERCENT",
        help="percentage taken off the whole order (0-100)",
    )
    add.set_defaults(handler=cmd_add)

    lst = sub.add_parser("list", help="list orders")
    lst.add_argument("--status", help="only orders with this status")
    lst.add_argument("--customer", help="only orders whose customer name contains this")
    add_date_range_args(lst)
    lst.set_defaults(handler=cmd_list)

    status = sub.add_parser("set-status", help="change the status of an order")
    status.add_argument("order_id")
    status.add_argument("status")
    status.set_defaults(handler=cmd_set_status)

    export = sub.add_parser("export", help="export orders as CSV")
    export.add_argument("--output", "-o", help="write to this file instead of stdout")
    export.add_argument(
        "--line-items",
        action="store_true",
        help="export one row per line item instead of one row per order",
    )
    add_date_range_args(export)
    export.add_argument("--status", help="only orders with this status")
    export.set_defaults(handler=cmd_export)

    customers = sub.add_parser("customers", help="export per-customer totals as CSV")
    customers.add_argument("--output", "-o", help="write to this file instead of stdout")
    add_date_range_args(customers)
    customers.set_defaults(handler=cmd_customers)

    report = sub.add_parser("report", help="print a summary report")
    report.set_defaults(handler=cmd_report)
    return parser


def add_date_range_args(parser):
    """Add the standard ``--from``/``--to`` (inclusive, YYYY-MM-DD) options."""
    parser.add_argument(
        "--from", dest="date_from", metavar="YYYY-MM-DD",
        help="only orders created on or after this date",
    )
    parser.add_argument(
        "--to", dest="date_to", metavar="YYYY-MM-DD",
        help="only orders created on or before this date (inclusive)",
    )


def date_range(args):
    """Parse ``--from``/``--to`` into a ``(date_from, date_to)`` pair of dates or None."""
    date_from = parse_date_arg(args.date_from) if args.date_from else None
    date_to = parse_date_arg(args.date_to) if args.date_to else None
    return date_from, date_to


def cmd_add(service, args, out):
    """Handle ``add``."""
    created_at = parse_timestamp(args.created_at) if args.created_at else None
    order = service.create_order(
        args.customer, args.email, args.items, created_at=created_at, discount_pct=args.discount
    )
    print(f"created {order.id} ({format_money(order.total)})", file=out)
    return 0


def cmd_list(service, args, out):
    """Handle ``list``."""
    date_from, date_to = date_range(args)
    orders = filter_orders(
        service.list_orders(status=args.status, customer=args.customer),
        date_from=date_from, date_to=date_to,
    )
    if not orders:
        print("no orders", file=out)
        return 0
    for order in orders:
        print(
            f"{order.id}  {format_timestamp(order.created_at)}  "
            f"{order.status.value:<9}  {format_money(order.total):>9}  {order.customer}",
            file=out,
        )
    return 0


def cmd_set_status(service, args, out):
    """Handle ``set-status``."""
    order = service.set_status(args.order_id, args.status)
    print(f"{order.id} is now {order.status.value}", file=out)
    return 0


def cmd_export(service, args, out):
    """Handle ``export``."""
    date_from, date_to = date_range(args)
    status = OrderStatus.parse(args.status) if args.status else None
    orders = filter_orders(
        service.list_orders(), date_from=date_from, date_to=date_to, status=status
    )
    writer = export_line_items_csv if args.line_items else export_orders_csv
    if args.output:
        count = write_output_file(args.output, lambda fh: writer(orders, fh))
        print(f"exported {count} rows to {args.output}", file=sys.stderr)
    else:
        writer(orders, out)
    return 0


def cmd_customers(service, args, out):
    """Handle ``customers``."""
    date_from, date_to = date_range(args)
    orders = filter_orders(service.list_orders(), date_from=date_from, date_to=date_to)
    if args.output:
        count = write_output_file(args.output, lambda fh: export_customers_csv(orders, fh))
        print(f"exported {count} rows to {args.output}", file=sys.stderr)
    else:
        export_customers_csv(orders, out)
    return 0


def cmd_report(service, args, out):
    """Handle ``report``."""
    print(render_summary(service.list_orders()), file=out)
    return 0


def main(argv=None, out=None):
    """Entry point. Returns the process exit status."""
    out = out or sys.stdout
    parser = build_parser()
    args = parser.parse_args(argv)
    service = OrderService(OrderStore(args.db))
    try:
        return args.handler(service, args, out)
    except OrderToolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
