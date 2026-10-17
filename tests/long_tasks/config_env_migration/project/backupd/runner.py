"""One backup run: collect files, archive, store, prune, notify."""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from . import archiver, filters, notifier, scheduler, storage
from .config import DEFAULT_SETTINGS_PATH, Config

log = logging.getLogger("backupd.runner")


@dataclass
class RunResult:
    archive: Optional[Path]
    files: int
    pruned: List[Path] = field(default_factory=list)
    notified: bool = False
    skipped: bool = False


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

    if not Path(cfg.source_dir).is_dir():
        raise FileNotFoundError(f"source_dir does not exist: {cfg.source_dir}")

    files = list(filters.iter_files(cfg))
    root = storage.ensure_backup_root(settings_path)
    archive = archiver.create_archive(cfg, files, now, root)
    archive = storage.store_archive(archive, settings_path)
    pruned = storage.prune_old_backups(now, settings_path)
    scheduler.save_last_run(cfg, now)
    log.info("wrote %s (%d files, pruned %d)", archive.name, len(files), len(pruned))

    mailer = notifier.build_notifier(settings_path, transport=transport)
    sent = mailer.send(
        "backup complete",
        f"Archive {archive.name} with {len(files)} files; pruned {len(pruned)} old backups.",
    )
    return RunResult(archive=archive, files=len(files), pruned=pruned, notified=sent is not None)
