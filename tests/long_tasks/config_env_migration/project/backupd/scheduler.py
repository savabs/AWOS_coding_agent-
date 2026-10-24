"""When is the next backup due?

The last successful run is recorded in ``<backup_dir>/.last_run``.
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

STATE_FILE = ".last_run"
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S"


def interval(cfg) -> timedelta:
    # Read from the raw parser so an edited settings.ini is honoured by a
    # long-running daemon that re-reads the parser between runs.
    minutes = cfg.parser.getint("backup", "interval_minutes", fallback=60)
    return timedelta(minutes=minutes)


def next_run(cfg, last_run: Optional[datetime], now: Optional[datetime] = None) -> datetime:
    """Time at which the next backup should start."""
    if last_run is None:
        return now or datetime.now()
    return last_run + interval(cfg)


def is_due(cfg, last_run: Optional[datetime], now: Optional[datetime] = None) -> bool:
    now = now or datetime.now()
    return next_run(cfg, last_run, now) <= now


def state_path(cfg) -> Path:
    return Path(cfg.backup_dir) / STATE_FILE


def load_last_run(cfg) -> Optional[datetime]:
    path = state_path(cfg)
    if not path.is_file():
        return None
    text = path.read_text().strip()
    try:
        return datetime.strptime(text, TIMESTAMP_FORMAT)
    except ValueError:
        return None


def save_last_run(cfg, when: datetime) -> None:
    path = state_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(when.strftime(TIMESTAMP_FORMAT) + "\n")
