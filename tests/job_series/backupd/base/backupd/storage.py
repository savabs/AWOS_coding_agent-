"""Where backups live on disk, and how old ones are pruned."""

import logging
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from .config import DEFAULT_SETTINGS_PATH, read_parser, resolve_setting

log = logging.getLogger("backupd.storage")

BACKUP_NAME_RE = re.compile(r"^backup-(\d{8}-\d{6})\.(zip|tar)$")
TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S"


def _read_storage_settings(settings_path=DEFAULT_SETTINGS_PATH):
    # Storage only needs two values; resolve them (environment first, then
    # settings.ini) without paying for a full load_config().
    parser = read_parser(settings_path)
    return (
        resolve_setting("backup_dir", parser=parser),
        resolve_setting("retention_days", parser=parser),
    )


def backup_root(settings_path=DEFAULT_SETTINGS_PATH) -> Path:
    """Directory that holds the backup archives."""
    return _read_storage_settings(settings_path)[0]


def retention_days(settings_path=DEFAULT_SETTINGS_PATH) -> int:
    """How many days of backups are kept."""
    return _read_storage_settings(settings_path)[1]


def ensure_backup_root(settings_path=DEFAULT_SETTINGS_PATH) -> Path:
    root = backup_root(settings_path)
    root.mkdir(parents=True, exist_ok=True)
    return root


def parse_backup_time(path: Path) -> Optional[datetime]:
    """Timestamp encoded in a backup file name, or None if not a backup."""
    match = BACKUP_NAME_RE.match(path.name)
    if not match:
        return None
    return datetime.strptime(match.group(1), TIMESTAMP_FORMAT)


def list_backups(root: Path) -> List[Path]:
    """Backup archives in *root*, oldest first."""
    if not root.is_dir():
        return []
    found = [p for p in root.iterdir() if p.is_file() and parse_backup_time(p)]
    return sorted(found, key=parse_backup_time)


def store_archive(archive: Path, settings_path=DEFAULT_SETTINGS_PATH) -> Path:
    """Move a freshly built archive into the backup root."""
    root = ensure_backup_root(settings_path)
    target = root / archive.name
    if archive.resolve() != target.resolve():
        shutil.move(str(archive), str(target))
    return target


def prune_old_backups(now=None, settings_path=DEFAULT_SETTINGS_PATH) -> List[Path]:
    """Delete backups older than the retention window; return what was removed."""
    now = now or datetime.now()
    root, keep_days = _read_storage_settings(settings_path)
    cutoff = now - timedelta(days=keep_days)
    removed = []
    for path in list_backups(root):
        if parse_backup_time(path) < cutoff:
            log.info("pruning %s", path.name)
            path.unlink()
            removed.append(path)
    return removed
