"""Configuration loading for backupd.

Settings live in an INI file (``settings.ini`` in the working directory by
default).  :func:`load_config` parses it into a typed :class:`Config`.

Sections and keys::

    [backup]   source_dir, backup_dir, interval_minutes, retention_days,
               compress, exclude
    [notify]   notify_email, smtp_host, smtp_port
    [logging]  log_level, log_file
"""

import configparser
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

DEFAULT_SETTINGS_PATH = "settings.ini"

VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class ConfigError(Exception):
    """Raised when the configuration is missing or invalid."""


@dataclass
class Config:
    """Typed view of the backupd settings."""

    source_dir: Path
    backup_dir: Path
    interval_minutes: int = 60
    retention_days: int = 7
    compress: bool = True
    exclude: List[str] = field(default_factory=list)
    notify_email: Optional[str] = None
    smtp_host: str = "localhost"
    smtp_port: int = 25
    log_level: str = "INFO"
    log_file: Optional[Path] = None
    # The raw parser is kept around so that callers can look at values that
    # are not (yet) modelled on the dataclass.
    parser: Optional[configparser.ConfigParser] = field(
        default=None, repr=False, compare=False
    )
    source_path: Optional[Path] = None


def read_parser(path=None) -> configparser.ConfigParser:
    """Read the INI file at *path* and return the raw parser."""
    ini_path = Path(path or DEFAULT_SETTINGS_PATH)
    if not ini_path.is_file():
        raise ConfigError(f"settings file not found: {ini_path}")
    parser = configparser.ConfigParser()
    try:
        parser.read(ini_path)
    except configparser.Error as exc:
        raise ConfigError(f"could not parse {ini_path}: {exc}") from exc
    return parser


def _required(parser, section, key):
    value = parser.get(section, key, fallback="").strip()
    if not value:
        raise ConfigError(f"missing required setting '{key}' in [{section}]")
    return value


def _int(parser, section, key, default):
    try:
        return parser.getint(section, key, fallback=default)
    except ValueError as exc:
        raise ConfigError(f"setting '{key}' must be an integer") from exc


def _bool(parser, section, key, default):
    try:
        return parser.getboolean(section, key, fallback=default)
    except ValueError as exc:
        raise ConfigError(f"setting '{key}' must be a boolean") from exc


def split_list(raw: str) -> List[str]:
    """Split a comma separated setting into a clean list."""
    return [item.strip() for item in raw.split(",") if item.strip()]


def _optional(parser, section, key):
    value = parser.get(section, key, fallback="").strip()
    return value or None


def load_config(path=None) -> Config:
    """Load and validate settings from the INI file at *path*."""
    parser = read_parser(path)

    source_dir = Path(_required(parser, "backup", "source_dir")).expanduser()
    backup_dir = Path(_required(parser, "backup", "backup_dir")).expanduser()

    interval = _int(parser, "backup", "interval_minutes", 60)
    if interval <= 0:
        raise ConfigError("setting 'interval_minutes' must be positive")
    retention = _int(parser, "backup", "retention_days", 7)
    if retention < 0:
        raise ConfigError("setting 'retention_days' must not be negative")

    log_level = parser.get("logging", "log_level", fallback="INFO").strip().upper()
    if log_level not in VALID_LOG_LEVELS:
        raise ConfigError(f"setting 'log_level' must be one of {VALID_LOG_LEVELS}")

    log_file = _optional(parser, "logging", "log_file")

    cfg = Config(
        source_dir=source_dir,
        backup_dir=backup_dir,
        interval_minutes=interval,
        retention_days=retention,
        compress=_bool(parser, "backup", "compress", True),
        exclude=split_list(parser.get("backup", "exclude", fallback="")),
        notify_email=_optional(parser, "notify", "notify_email"),
        smtp_host=parser.get("notify", "smtp_host", fallback="localhost").strip(),
        smtp_port=_int(parser, "notify", "smtp_port", 25),
        log_level=log_level,
        log_file=Path(log_file).expanduser() if log_file else None,
        parser=parser,
        source_path=Path(path or DEFAULT_SETTINGS_PATH),
    )
    logging.getLogger("backupd").debug("loaded settings from %s", cfg.source_path)
    return cfg
