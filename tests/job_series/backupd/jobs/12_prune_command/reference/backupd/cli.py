"""Command line interface: ``python -m backupd <command>``.

Each command is a ``cmd_<name>(args) -> int`` handler registered on its
subparser with ``set_defaults(handler=...)``.  :func:`main` configures
logging, calls the handler and turns :class:`BackupdError` into the usual
one-line messages and exit codes.
"""

import argparse
import logging
import sys
from datetime import datetime

from . import archiver, health, runner, scheduler, storage
from .config import DEFAULT_SETTINGS_PATH, load_config
from .errors import BackupdError, ConfigError, RestoreError
from .logging_setup import configure_logging
from .paths import expand_path
from .units import format_size

log = logging.getLogger("backupd.cli")


def cmd_run(args) -> int:
    cfg = load_config(args.config)
    result = runner.run_backup(cfg, args.config, force=not args.if_due)
    if result.skipped:
        print("backup not due")
    else:
        print(f"backup written: {result.archive} ({result.files} files, "
              f"{format_size(result.size)})")
    return 0


def cmd_status(args) -> int:
    report = health.status_report(args.config)
    if args.json:
        print(health.format_report_json(report))
    else:
        print(health.format_report(report))
    return 0


def cmd_due(args) -> int:
    cfg = load_config(args.config)
    due = scheduler.is_due(cfg, scheduler.load_last_run(cfg), datetime.now())
    print("due" if due else "not due")
    return 0 if due else 1


def _show_config(cfg) -> str:
    fields = [
        "source_dir", "backup_dir", "interval_minutes", "retention_days", "keep_last", "compress",
        "exclude", "min_free_space", "notify_email", "smtp_host", "smtp_port", "notify_on",
        "log_level", "log_file",
    ]
    return "\n".join(f"{name} = {getattr(cfg, name)!r}" for name in fields)


def cmd_show_config(args) -> int:
    print(_show_config(load_config(args.config)))
    return 0


def _list_backups(settings_path) -> str:
    # Like status: only needs backup_dir, so it works without a full config.
    root = storage.backup_root(settings_path)
    backups = storage.list_backups(root)
    if not backups:
        return f"no backups in {root}"
    lines = []
    total = 0
    for path in backups:
        size = path.stat().st_size
        total += size
        taken = storage.parse_backup_time(path)
        lines.append(f"{path.name}  {taken:%Y-%m-%d %H:%M:%S}  {format_size(size)}")
    noun = "backup" if len(backups) == 1 else "backups"
    lines.append(f"{len(backups)} {noun}, {format_size(total)} total")
    return "\n".join(lines)


def cmd_list(args) -> int:
    print(_list_backups(args.config))
    return 0


def cmd_verify(args) -> int:
    # Only needs backup_dir, like status and list.
    root = storage.backup_root(args.config)
    path = storage.find_backup(root, args.name)
    files = archiver.verify_archive(path)
    log.info("verified %s (%d files)", path.name, files)
    print(f"ok: {path.name} ({files} files)")
    return 0


def cmd_restore(args) -> int:
    # Only needs backup_dir, like status, list and verify.
    root = storage.backup_root(args.config)
    path = storage.find_backup(root, args.name)
    dest = expand_path(args.dest)
    if dest.exists() and not dest.is_dir():
        raise RestoreError(f"destination is not a directory: {dest}")
    if dest.is_dir() and any(dest.iterdir()):
        raise RestoreError(f"destination is not empty: {dest}")
    try:
        dest.mkdir(parents=True, exist_ok=True)
        files = archiver.extract_archive(path, dest)
    except OSError as exc:
        raise RestoreError(f"could not restore {path.name} to {dest}: {exc}") from exc
    log.info("restored %s to %s (%d files)", path.name, dest, files)
    print(f"restored {files} files from {path.name} to {dest}")
    return 0


def cmd_prune(args) -> int:
    # Only needs the storage settings (backup_dir, retention_days, keep_last).
    now = datetime.now()
    selected = storage.backups_to_prune(now, args.config)
    if not selected:
        print("nothing to prune")
        return 0
    freed = sum(path.stat().st_size for path in selected)
    if not args.dry_run:
        storage.prune_old_backups(now, args.config)
    verb = "would prune" if args.dry_run else "pruned"
    for path in selected:
        print(f"{verb} {path.name}")
    noun = "backup" if len(selected) == 1 else "backups"
    if args.dry_run:
        print(f"{len(selected)} {noun} would be pruned, {format_size(freed)} would be freed")
    else:
        print(f"{len(selected)} {noun} pruned, {format_size(freed)} freed")
    return 0


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
    run.set_defaults(handler=cmd_run)
    status = sub.add_parser("status", help="print backup status")
    status.add_argument("--json", action="store_true", help="print the report as JSON")
    status.set_defaults(handler=cmd_status)
    sub.add_parser("due", help="exit 0 if a backup is due, 1 otherwise").set_defaults(
        handler=cmd_due)
    sub.add_parser("show-config", help="print the effective settings").set_defaults(
        handler=cmd_show_config)
    sub.add_parser("list", help="list the backups, oldest first").set_defaults(handler=cmd_list)
    verify = sub.add_parser("verify", help="check that a backup can be read back")
    verify.add_argument("name", nargs="?", help="archive file name (default: the latest)")
    verify.set_defaults(handler=cmd_verify)
    restore = sub.add_parser("restore", help="extract a backup into an empty directory")
    restore.add_argument("name", help="archive file name, or 'latest'")
    restore.add_argument("dest", help="directory to restore into (must be empty or new)")
    restore.set_defaults(handler=cmd_restore)
    prune = sub.add_parser("prune", help="delete backups past retention now")
    prune.add_argument("--dry-run", action="store_true", help="only show what would be deleted")
    prune.set_defaults(handler=cmd_prune)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.config)
    try:
        return args.handler(args)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    except BackupdError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
