"""Command-line interface: python -m fintrack [--db PATH] COMMAND ..."""
from __future__ import annotations

import argparse
import datetime
import os
import re
import sys
from typing import List, Optional

from fintrack.budgets import check_budgets
from fintrack.errors import FintrackError, ValidationError
from fintrack.export import EXPORT_FORMATS, export_transactions
from fintrack.importers import SUPPORTED_FORMATS, parse_csv
from fintrack.models import fmt_money, to_money
from fintrack.reports import monthly_report, render_json, render_text
from fintrack.rules import categorize_ledger, load_rules
from fintrack.storage import Ledger

EXIT_OK, EXIT_ERROR, EXIT_ROW_ERRORS = 0, 1, 3


def _month(text: str):
    m = re.fullmatch(r"(\d{4})-(\d{2})", text.strip())
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise ValidationError(f"invalid month {text!r} (expected YYYY-MM)")
    return int(m.group(1)), int(m.group(2))


def _date(text: Optional[str]):
    if text is None:
        return None
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text.strip()):
            raise ValueError
        return datetime.date.fromisoformat(text.strip())
    except ValueError:
        raise ValidationError(f"invalid date {text!r} (expected YYYY-MM-DD)") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fintrack", description="Personal finance tracker")
    parser.add_argument("--db", help="SQLite database (default: $FINTRACK_DB or ./fintrack.db)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("import", help="import bank CSV files")
    p.add_argument("files", nargs="+")
    p.add_argument("--format", choices=SUPPORTED_FORMATS)
    p.add_argument("--account", default="default")

    p = sub.add_parser("categorize", help="apply categorization rules")
    p.add_argument("--rules", required=True)
    p.add_argument("--all", action="store_true", help="recategorize every transaction")

    p = sub.add_parser("report", help="monthly report")
    p.add_argument("--month", required=True)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("budget", help="manage budgets")
    bsub = p.add_subparsers(dest="budget_command", required=True)
    b = bsub.add_parser("set")
    b.add_argument("category")
    b.add_argument("amount")
    bsub.add_parser("list")
    b = bsub.add_parser("check")
    b.add_argument("--month", required=True)

    p = sub.add_parser("export", help="export transactions")
    p.add_argument("--format", choices=EXPORT_FORMATS, required=True)
    p.add_argument("--from", dest="start")
    p.add_argument("--to", dest="end")
    p.add_argument("--category")
    p.add_argument("--output")
    return parser


def _cmd_import(ledger: Ledger, args) -> int:
    results = [(path, parse_csv(path, fmt=args.format, account=args.account)) for path in args.files]
    had_errors = False
    for path, result in results:
        added = ledger.add_transactions(result.transactions)
        for err in result.errors:
            print(f"{path}:{err.line}: {err.message}", file=sys.stderr)
        had_errors = had_errors or bool(result.errors)
        print(f"Imported {path}: {added.added} added, {added.duplicates} duplicates, "
              f"{len(result.errors)} errors")
    return EXIT_ROW_ERRORS if had_errors else EXIT_OK


def _cmd_budget(ledger: Ledger, args) -> int:
    if args.budget_command == "set":
        amount = to_money(args.amount)
        ledger.set_budget(args.category, amount)
        print(f"Budget for {args.category} set to {fmt_money(amount)}")
    elif args.budget_command == "list":
        budgets = ledger.get_budgets()
        if not budgets:
            print("No budgets set.")
        for cat in sorted(budgets):
            print(f"{cat}: {fmt_money(budgets[cat])}")
    else:
        statuses = check_budgets(ledger, *_month(args.month))
        if not statuses:
            print("No budgets set.")
        for s in statuses:
            print(f"{s.category}: spent {fmt_money(s.spent)} of {fmt_money(s.budget)} ({s.status.upper()})")
    return EXIT_OK


def _cmd_export(ledger: Ledger, args) -> int:
    start, end = _date(args.start), _date(args.end)
    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8", newline="") as fh:
                n = export_transactions(ledger, fh, args.format, start, end, args.category)
        except OSError as exc:
            raise ValidationError(f"cannot write {args.output}: {exc.strerror}") from None
        print(f"Exported {n} transactions to {args.output}")
    else:
        export_transactions(ledger, sys.stdout, args.format, start, end, args.category)
    return EXIT_OK


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    db = args.db or os.environ.get("FINTRACK_DB") or "fintrack.db"
    try:
        # Validate cheap arguments before touching the database.
        if args.command == "report":
            year, month = _month(args.month)
        if args.command == "categorize":
            ruleset = load_rules(args.rules)
        with Ledger(db) as ledger:
            if args.command == "import":
                return _cmd_import(ledger, args)
            if args.command == "categorize":
                n = categorize_ledger(ledger, ruleset, recategorize=args.all)
                print(f"Categorized {n} transactions")
                return EXIT_OK
            if args.command == "report":
                report = monthly_report(ledger, year, month)
                out = render_json(report) + "\n" if args.json else render_text(report)
                sys.stdout.write(out)
                return EXIT_OK
            if args.command == "budget":
                return _cmd_budget(ledger, args)
            return _cmd_export(ledger, args)
    except FintrackError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:  # sqlite / OS problems: still no traceback
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
