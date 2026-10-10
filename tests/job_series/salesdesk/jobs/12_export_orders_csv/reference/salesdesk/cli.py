"""Command line interface.

Examples::

    python -m salesdesk.cli --db sales.db add-customer acme "Acme" --timezone Europe/Berlin
    python -m salesdesk.cli --db sales.db import orders.csv
    python -m salesdesk.cli --db sales.db report acme
    python -m salesdesk.cli --db sales.db --now 2026-03-10T09:00:00Z report acme
    python -m salesdesk.cli --db sales.db breakdown acme 2026-03-01 2026-03-07 --csv
    python -m salesdesk.cli --db sales.db week acme
    python -m salesdesk.cli --db sales.db orders acme --yesterday
    python -m salesdesk.cli --db sales.db monthly acme 2026 --csv
    python -m salesdesk.cli --db sales.db export acme 2026-03-01 2026-03-31 > march.csv

Adding a command: write a ``cmd_<name>(service, args, out) -> int`` handler
and register it in :func:`build_parser` with ``set_defaults(handler=...)``.
Handlers raise ``ValueError`` / ``UnknownCustomer`` / ``UnknownOrder`` for bad input; ``main``
turns those into the one-line ``error: ...`` output.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import timedelta
from typing import List, Optional

from .clock import FixedClock, SystemClock, parse_instant
from .currency import format_cents
from .importer import import_orders
from .render import (render_breakdown_csv, render_breakdown_text, render_daily_json, render_daily_text,
                     render_monthly_csv, render_monthly_text, render_order_lines,
                     render_orders_csv)
from .service import ReportService
from .settings import SettingsStore, UnknownCustomer
from .storage import Storage, UnknownOrder
from .timeutil import parse_day


# -- handlers ---------------------------------------------------------------

def cmd_add_customer(service: ReportService, args, out) -> int:
    c = service.settings.register(args.customer_id, args.name, args.timezone, args.currency)
    out.write(f"registered {c.customer_id} ({c.timezone})\n")
    return 0


def cmd_set_timezone(service: ReportService, args, out) -> int:
    c = service.settings.set_timezone(args.customer_id, args.timezone)
    out.write(f"updated {c.customer_id} ({c.timezone})\n")
    return 0


def cmd_import(service: ReportService, args, out) -> int:
    try:
        with open(args.csv_path, newline="", encoding="utf-8") as fh:
            result = import_orders(service.storage, fh)
    except OSError as exc:
        raise ValueError(f"cannot read {args.csv_path}: {exc.strerror or exc}") from None
    out.write(f"imported {result.imported} orders\n")
    for err in result.errors:
        out.write(f"  skipped {err}\n")
    return 0


def cmd_report(service: ReportService, args, out) -> int:
    if args.yesterday:
        report = service.yesterday_report(args.customer_id)
    else:
        day = parse_day(args.day) if args.day else None
        report = service.daily_report(args.customer_id, day)
    out.write(render_daily_json(report) if args.json else render_daily_text(report))
    return 0


def cmd_export(service: ReportService, args, out) -> int:
    customer, orders = service.orders_between_days(
        args.customer_id, parse_day(args.first), parse_day(args.last))
    out.write(render_orders_csv(orders, customer))
    return 0


def cmd_summary(service: ReportService, args, out) -> int:
    day = parse_day(args.day) if args.day else None
    customers = service.settings.all()
    if not customers:
        out.write("no customers\n")
        return 0
    for c in customers:
        report = service.daily_report(c.customer_id, day)
        out.write(f"{c.customer_id}  {report.day.isoformat()}  orders: {report.order_count}  "
                  f"total: {format_cents(report.total_cents, c.currency)}\n")
    return 0


def cmd_orders(service: ReportService, args, out) -> int:
    if args.yesterday:
        day = service.current_day(args.customer_id) - timedelta(days=1)
    else:
        day = parse_day(args.day) if args.day else None
    customer, _, orders = service.day_orders(args.customer_id, day)
    out.write(render_order_lines(orders, customer))
    return 0


def cmd_monthly(service: ReportService, args, out) -> int:
    customer, rows = service.monthly(args.customer_id, args.year)
    if args.csv:
        out.write(render_monthly_csv(rows))
    else:
        out.write(render_monthly_text(rows, customer.currency))
    return 0


def cmd_refund(service: ReportService, args, out) -> int:
    order, customer = service.refund(args.order_id)
    out.write(f"refunded {order.order_id} ({format_cents(order.amount_cents, customer.currency)})\n")
    return 0


def _write_breakdown(service: ReportService, customer_id: str, rows, as_csv: bool, out) -> int:
    customer = service.settings.get(customer_id)
    if as_csv:
        out.write(render_breakdown_csv(rows))
    else:
        out.write(render_breakdown_text(rows, customer.currency))
    return 0


def cmd_breakdown(service: ReportService, args, out) -> int:
    service.settings.get(args.customer_id)
    rows = service.breakdown(args.customer_id, parse_day(args.first), parse_day(args.last))
    return _write_breakdown(service, args.customer_id, rows, args.csv, out)


def cmd_week(service: ReportService, args, out) -> int:
    rows = service.week_to_date(args.customer_id)
    return _write_breakdown(service, args.customer_id, rows, args.csv, out)


# -- parser -----------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="salesdesk")
    parser.add_argument("--db", default="salesdesk.db")
    parser.add_argument("--now", help="pin the current time (ISO-8601)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("add-customer")
    p.add_argument("customer_id")
    p.add_argument("name")
    p.add_argument("--timezone", default="UTC")
    p.add_argument("--currency", default="USD")
    p.set_defaults(handler=cmd_add_customer)

    p = sub.add_parser("set-timezone")
    p.add_argument("customer_id")
    p.add_argument("timezone")
    p.set_defaults(handler=cmd_set_timezone)

    p = sub.add_parser("import")
    p.add_argument("csv_path")
    p.set_defaults(handler=cmd_import)

    p = sub.add_parser("report")
    p.add_argument("customer_id")
    p.add_argument("--day", help="YYYY-MM-DD (default: today)")
    p.add_argument("--yesterday", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(handler=cmd_report)

    p = sub.add_parser("export")
    p.add_argument("customer_id")
    p.add_argument("first")
    p.add_argument("last")
    p.set_defaults(handler=cmd_export)

    p = sub.add_parser("summary")
    p.add_argument("--day", help="YYYY-MM-DD (default: each merchant's today)")
    p.set_defaults(handler=cmd_summary)

    p = sub.add_parser("orders")
    p.add_argument("customer_id")
    p.add_argument("--day", help="YYYY-MM-DD (default: today)")
    p.add_argument("--yesterday", action="store_true")
    p.set_defaults(handler=cmd_orders)

    p = sub.add_parser("monthly")
    p.add_argument("customer_id")
    p.add_argument("year", type=int)
    p.add_argument("--csv", action="store_true")
    p.set_defaults(handler=cmd_monthly)

    p = sub.add_parser("refund")
    p.add_argument("order_id")
    p.set_defaults(handler=cmd_refund)

    p = sub.add_parser("breakdown")
    p.add_argument("customer_id")
    p.add_argument("first")
    p.add_argument("last")
    p.add_argument("--csv", action="store_true")
    p.set_defaults(handler=cmd_breakdown)

    p = sub.add_parser("week")
    p.add_argument("customer_id")
    p.add_argument("--csv", action="store_true")
    p.set_defaults(handler=cmd_week)
    return parser


def _clock(now: Optional[str]):
    if not now:
        return SystemClock()
    try:
        return FixedClock(parse_instant(now))
    except ValueError:
        raise ValueError(f"invalid --now value: {now!r}") from None


def _open_storage(path: str) -> Storage:
    try:
        return Storage(path)
    except sqlite3.Error as exc:
        raise ValueError(f"cannot open database {path}: {exc}") from None


def main(argv: Optional[List[str]] = None, out=None) -> int:
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    storage = None
    try:
        clock = _clock(args.now)
        storage = _open_storage(args.db)
        service = ReportService(storage, SettingsStore(storage), clock)
        return args.handler(service, args, out)
    except UnknownCustomer as exc:
        out.write(f"error: unknown customer {exc.args[0]}\n")
        return 2
    except UnknownOrder as exc:
        out.write(f"error: unknown order {exc.args[0]}\n")
        return 2
    except ValueError as exc:
        out.write(f"error: {exc}\n")
        return 2
    finally:
        if storage is not None:
            storage.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
