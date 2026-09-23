"""Command line interface.

    python -m ledger demo --seed 7 --size large --year 2025 --month 11
    python -m ledger generate --seed 7 --size medium --out ledger.csv
    python -m ledger summary --seed 7 --size large --account ACC-0007 --year 2025 --month 11 --json
"""

import argparse
import sys
import time

from .fixtures import SIZES, generate_ledger
from .report import render_json, render_text
from .summary import build_monthly_summary


def _parser():
    parser = argparse.ArgumentParser(prog="ledger", description="Ledgerly account tools")
    sub = parser.add_subparsers(dest="command", required=True)

    def data_args(p):
        p.add_argument("--seed", type=int, default=7)
        p.add_argument("--size", choices=sorted(SIZES), default="small")

    demo = sub.add_parser("demo", help="generate data and print a summary")
    data_args(demo)
    demo.add_argument("--year", type=int, default=2025)
    demo.add_argument("--month", type=int, default=11)
    demo.add_argument("--daily", action="store_true")

    summary = sub.add_parser("summary", help="monthly summary for one account")
    data_args(summary)
    summary.add_argument("--account", default=None, help="defaults to the primary account")
    summary.add_argument("--year", type=int, required=True)
    summary.add_argument("--month", type=int, required=True)
    summary.add_argument("--json", action="store_true")
    summary.add_argument("--daily", action="store_true")
    summary.add_argument("--timing", action="store_true", help="print elapsed time to stderr")

    gen = sub.add_parser("generate", help="write generated ledger rows to CSV")
    data_args(gen)
    gen.add_argument("--out", required=True)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    bundle = generate_ledger(args.seed, args.size)
    if args.command == "generate":
        bundle.store.save_csv(args.out)
        print(f"wrote {len(bundle.store)} rows to {args.out}")
        return 0
    account_id = getattr(args, "account", None) or bundle.account_id
    started = time.perf_counter()
    result = build_monthly_summary(bundle.store, bundle.fx, bundle.categoriser,
                                   account_id, args.year, args.month)
    elapsed = time.perf_counter() - started
    if getattr(args, "json", False):
        sys.stdout.write(render_json(result))
    else:
        sys.stdout.write(render_text(result, show_daily=args.daily))
    if args.command == "demo" or getattr(args, "timing", False):
        print(f"summary built in {elapsed:.3f}s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
