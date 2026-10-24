"""One backup run: collect files, archive, store, prune, notify."""

import logging
import shutil
import smtplib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from . import archiver, filters, notifier, scheduler, storage
from .config import DEFAULT_SETTINGS_PATH, Config
from .errors import BackupdError, SourceError, SpaceError
from .units import format_size

log = logging.getLogger("backupd.runner")


@dataclass
class RunResult:
    archive: Optional[Path]
    files: int
    pruned: List[Path] = field(default_factory=list)
    notified: bool = False
    skipped: bool = False
    size: int = 0


def _check_free_space(root: Path, required: int) -> None:
    """Refuse to start when the backup disk has less than *required* bytes free."""
    if not required:
        return
    free = shutil.disk_usage(root).free
    if free < required:
        raise SpaceError(
            f"not enough free space in {root}: {format_size(free)} free, "
            f"{format_size(required)} required"
        )


def _notify(settings_path, transport, subject: str, body: str) -> bool:
    """Send a notification; a mail problem is logged, never raised."""
    mailer = notifier.build_notifier(settings_path, transport=transport)
    try:
        return mailer.send(subject, body) is not None
    except (OSError, smtplib.SMTPException) as exc:
        log.warning("could not send notification %r: %s", subject, exc)
        return False


def _backup(cfg: Config, settings_path, now: datetime) -> RunResult:
    source = Path(cfg.source_dir)
    if not source.exists():
        raise SourceError(f"source directory does not exist: {source}")
    if not source.is_dir():
        raise SourceError(f"source directory is not a directory: {source}")

    files = list(filters.iter_files(cfg))
    root = storage.ensure_backup_root(settings_path)
    _check_free_space(root, cfg.min_free_space)
    archive = archiver.create_archive(cfg, files, now, root)
    archive = storage.store_archive(archive, settings_path)
    pruned = storage.prune_old_backups(now, settings_path)
    scheduler.save_last_run(cfg, now)
    size = archive.stat().st_size
    log.info("wrote %s (%d files, %s, pruned %d)", archive.name, len(files),
             format_size(size), len(pruned))
    return RunResult(archive=archive, files=len(files), pruned=pruned, size=size)


def run_backup(
    cfg: Config,
    settings_path=DEFAULT_SETTINGS_PATH,
    now: Optional[datetime] = None,
    force: bool = True,
    transport=None,
) -> RunResult:
    now = now or datetime.now()
    last = scheduler.load_last_run(cfg)
    if not force and not scheduler.is_due(cfg, last, now):
        log.info("backup not due until %s", scheduler.next_run(cfg, last, now))
        return RunResult(archive=None, files=0, skipped=True)

    try:
        result = _backup(cfg, settings_path, now)
    except BackupdError as exc:
        log.error("backup failed: %s", exc)
        if cfg.notify_on in ("always", "failure"):
            _notify(settings_path, transport, "backup failed", f"Backup failed: {exc}")
        raise

    if cfg.notify_on == "always":
        result.notified = _notify(
            settings_path, transport, "backup complete",
            f"Archive {result.archive.name} with {result.files} files "
            f"({format_size(result.size)}); pruned {len(result.pruned)} old backups.",
        )
    return result
