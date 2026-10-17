"""Command line interface: ``python -m backupd <command>``."""

import argparse
import sys
from datetime import datetime

from . import health, runner, scheduler
from .config import DEFAULT_SETTINGS_PATH, ConfigError, load_config
from .logging_setup import configure_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="backupd", description="Simple file backup scheduler")
    parser.add_argument(
        "--config",
        default=DEFAULT_SETTINGS_PATH,
        help="path to settings.ini (default: ./settings.ini)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run a backup now")
    run.add_argument("--if-due", action="store_true", help="only run if the interval has passed")
    sub.add_parser("status", help="print backup status")
    sub.add_parser("due", help="exit 0 if a backup is due, 1 otherwise")
    sub.add_parser("show-config", help="print the effective settings")
    return parser


def _show_config(cfg) -> str:
    fields = [
        "source_dir", "backup_dir", "interval_minutes", "retention_days", "compress",
        "exclude", "notify_email", "smtp_host", "smtp_port", "log_level", "log_file",
    ]
    return "\n".join(f"{name} = {getattr(cfg, name)!r}" for name in fields)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.config)
    try:
        if args.command == "status":
            print(health.format_report(health.status_report(args.config)))
            return 0
        cfg = load_config(args.config)
        if args.command == "show-config":
            print(_show_config(cfg))
            return 0
        if args.command == "due":
            due = scheduler.is_due(cfg, scheduler.load_last_run(cfg), datetime.now())
            print("due" if due else "not due")
            return 0 if due else 1
        result = runner.run_backup(cfg, args.config, force=not args.if_due)
        if result.skipped:
            print("backup not due")
        else:
            print(f"backup written: {result.archive} ({result.files} files)")
        return 0
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
