"""Lightweight status report used by ``backupd status`` and monitoring.

Monitoring probes call this often, so it avoids the full config validation
and resolves just what it needs (environment first, then settings.ini).
"""

import json
from datetime import datetime

from .config import DEFAULT_SETTINGS_PATH, read_parser, resolve_setting
from .storage import list_backups, parse_backup_time
from .units import format_size


def status_report(settings_path=DEFAULT_SETTINGS_PATH, now=None) -> dict:
    parser = read_parser(settings_path)
    backup_dir = resolve_setting("backup_dir", parser=parser)
    interval = resolve_setting("interval_minutes", parser=parser)
    notify = bool(resolve_setting("notify_email", parser=parser))

    backups = list_backups(backup_dir)
    latest = parse_backup_time(backups[-1]) if backups else None
    oldest = parse_backup_time(backups[0]) if backups else None
    total = sum(path.stat().st_size for path in backups)
    now = now or datetime.now()
    overdue = latest is None or (now - latest).total_seconds() > interval * 60 * 2
    return {
        "backup_dir": str(backup_dir),
        "interval_minutes": interval,
        "notifications": notify,
        "backups": len(backups),
        "latest": latest.isoformat() if latest else None,
        "overdue": overdue,
        "oldest": oldest.isoformat() if oldest else None,
        "total_size": format_size(total),
    }


def format_report(report: dict) -> str:
    lines = [f"{key}: {value}" for key, value in report.items()]
    return "\n".join(lines)


def format_report_json(report: dict) -> str:
    return json.dumps(report, indent=2)
