"""Command line interface.

Examples::

    python -m salesdesk.cli --db sales.db add-customer acme "Acme" --timezone Europe/Berlin
    python -m salesdesk.cli --db sales.db import orders.csv
    python -m salesdesk.cli --db sales.db report acme
    python -m salesdesk.cli --db sales.db --now 2026-03-10T09:00:00Z report acme
    python -m salesdesk.cli --db sales.db breakdown acme 2026-03-01 2026-03-07 --csv
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from .clock import FixedClock, SystemClock, parse_instant
from .importer import import_orders
from .render import render_breakdown_csv, render_breakdown_text, render_daily_text
from .service import ReportService
from .settings import SettingsStore, UnknownCustomer
from .storage import Storage
from .timeutil import parse_day


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

    p = sub.add_parser("import")
    p.add_argument("csv_path")

    p = sub.add_parser("report")
    p.add_argument("customer_id")
    p.add_argument("--day", help="YYYY-MM-DD (default: today)")
    p.add_argument("--yesterday", action="store_true")

    p = sub.add_parser("breakdown")
    p.add_argument("customer_id")
    p.add_argument("first")
    p.add_argument("last")
    p.add_argument("--csv", action="store_true")
    return parser


def main(argv: Optional[List[str]] = None, out=None) -> int:
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    clock = FixedClock(parse_instant(args.now)) if args.now else SystemClock()
    storage = Storage(args.db)
    try:
        settings = SettingsStore(storage)
        service = ReportService(storage, settings, clock)
        if args.command == "add-customer":
            c = settings.register(args.customer_id, args.name, args.timezone, args.currency)
            out.write(f"registered {c.customer_id} ({c.timezone})\n")
        elif args.command == "import":
            with open(args.csv_path, newline="", encoding="utf-8") as fh:
                result = import_orders(storage, fh)
            out.write(f"imported {result.imported} orders\n")
            for err in result.errors:
                out.write(f"  skipped {err}\n")
        elif args.command == "report":
            if args.yesterday:
                report = service.yesterday_report(args.customer_id)
            else:
                day = parse_day(args.day) if args.day else None
                report = service.daily_report(args.customer_id, day)
            out.write(render_daily_text(report))
        elif args.command == "breakdown":
            customer = settings.get(args.customer_id)
            rows = service.breakdown(args.customer_id, parse_day(args.first), parse_day(args.last))
            if args.csv:
                out.write(render_breakdown_csv(rows))
            else:
                out.write(render_breakdown_text(rows, customer.currency))
        return 0
    except UnknownCustomer as exc:
        out.write(f"error: unknown customer {exc.args[0]}\n")
        return 2
    except ValueError as exc:
        out.write(f"error: {exc}\n")
        return 2
    finally:
        storage.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
