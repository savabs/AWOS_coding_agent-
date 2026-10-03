"""Lightweight status report used by ``backupd status`` and monitoring.

Monitoring probes call this often, so it avoids the full config validation
and reads just what it needs from the settings file.
"""

import configparser
from datetime import datetime
from pathlib import Path

from .config import DEFAULT_SETTINGS_PATH, ConfigError
from .storage import list_backups, parse_backup_time


def status_report(settings_path=DEFAULT_SETTINGS_PATH, now=None) -> dict:
    parser = configparser.ConfigParser()
    if not parser.read(settings_path):
        raise ConfigError(f"settings file not found: {settings_path}")
    raw_dir = parser.get("backup", "backup_dir", fallback="").strip()
    if not raw_dir:
        raise ConfigError("missing required setting 'backup_dir' in [backup]")
    backup_dir = Path(raw_dir).expanduser()
    interval = parser.getint("backup", "interval_minutes", fallback=60)
    notify = bool(parser.get("notify", "notify_email", fallback="").strip())

    backups = list_backups(backup_dir)
    latest = parse_backup_time(backups[-1]) if backups else None
    now = now or datetime.now()
    overdue = latest is None or (now - latest).total_seconds() > interval * 60 * 2
    return {
        "backup_dir": str(backup_dir),
        "interval_minutes": interval,
        "notifications": notify,
        "backups": len(backups),
        "latest": latest.isoformat() if latest else None,
        "overdue": overdue,
    }


def format_report(report: dict) -> str:
    lines = [f"{key}: {value}" for key, value in report.items()]
    return "\n".join(lines)
