"""Configuration loading for backupd.

Every setting can come from an environment variable named ``APP_`` plus the
key in upper case (``interval_minutes`` -> ``APP_INTERVAL_MINUTES``).  The
legacy INI file (``settings.ini`` in the working directory by default) is
still read when present; for each key the environment variable wins.

Sections and keys::

    [backup]   source_dir, backup_dir, interval_minutes, retention_days,
               compress, exclude
    [notify]   notify_email, smtp_host, smtp_port
    [logging]  log_level, log_file
"""

import configparser
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

DEFAULT_SETTINGS_PATH = "settings.ini"
ENV_PREFIX = "APP_"

VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")

_REQUIRED = object()

# key -> (ini section, type, default)
SETTINGS = {
    "source_dir": ("backup", "path", _REQUIRED),
    "backup_dir": ("backup", "path", _REQUIRED),
    "interval_minutes": ("backup", "int", 60),
    "retention_days": ("backup", "int", 7),
    "compress": ("backup", "bool", True),
    "exclude": ("backup", "list", ()),
    "notify_email": ("notify", "str", None),
    "smtp_host": ("notify", "str", "localhost"),
    "smtp_port": ("notify", "int", 25),
    "log_level": ("logging", "str", "INFO"),
    "log_file": ("logging", "path", None),
}


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
    source_path: Optional[Path] = None


def env_name(key: str) -> str:
    return ENV_PREFIX + key.upper()


def read_parser(path=None) -> configparser.ConfigParser:
    """Read the INI file at *path* if it exists (an empty parser otherwise)."""
    parser = configparser.ConfigParser()
    ini_path = Path(path or DEFAULT_SETTINGS_PATH)
    if ini_path.is_file():
        try:
            parser.read(ini_path)
        except configparser.Error as exc:
            raise ConfigError(f"could not parse {ini_path}: {exc}") from exc
    return parser


def split_list(raw: str) -> List[str]:
    """Split a comma separated setting into a clean list."""
    return [item.strip() for item in raw.split(",") if item.strip()]


def parse_bool(raw: str, key: str = "value") -> bool:
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError(f"setting '{key}' must be a boolean (true/false, yes/no, 1/0)")


def _convert(key, kind, raw):
    if kind == "int":
        try:
            return int(raw)
        except ValueError as exc:
            raise ConfigError(f"setting '{key}' must be an integer, got {raw!r}") from exc
    if kind == "bool":
        return parse_bool(raw, key)
    if kind == "list":
        return split_list(raw)
    if kind == "path":
        return Path(raw).expanduser()
    return raw


def _raw_value(key, parser, environ):
    section = SETTINGS[key][0]
    value = environ.get(env_name(key))
    if value is None or not value.strip():
        value = parser.get(section, key, fallback=None)
    if value is None or not value.strip():
        return None
    return value.strip()


def resolve_setting(key, path=None, environ=None, parser=None):
    """Typed value of one setting: environment first, then the INI file."""
    if key not in SETTINGS:
        raise KeyError(key)
    environ = os.environ if environ is None else environ
    parser = read_parser(path) if parser is None else parser
    section, kind, default = SETTINGS[key]
    raw = _raw_value(key, parser, environ)
    if raw is None:
        if default is _REQUIRED:
            raise ConfigError(
                f"missing required setting '{key}': set {env_name(key)} "
                f"or '{key}' in [{section}] of settings.ini"
            )
        return list(default) if kind == "list" else default
    return _convert(key, kind, raw)


def load_config(path=None, environ=None) -> Config:
    """Load and validate settings from the environment and the INI file."""
    parser = read_parser(path)
    values = {key: resolve_setting(key, environ=environ, parser=parser) for key in SETTINGS}

    if values["interval_minutes"] <= 0:
        raise ConfigError("setting 'interval_minutes' must be positive")
    if values["retention_days"] < 0:
        raise ConfigError("setting 'retention_days' must not be negative")
    values["log_level"] = values["log_level"].upper()
    if values["log_level"] not in VALID_LOG_LEVELS:
        raise ConfigError(f"setting 'log_level' must be one of {VALID_LOG_LEVELS}")

    cfg = Config(source_path=Path(path or DEFAULT_SETTINGS_PATH), **values)
    logging.getLogger("backupd").debug("loaded settings (ini: %s)", cfg.source_path)
    return cfg
