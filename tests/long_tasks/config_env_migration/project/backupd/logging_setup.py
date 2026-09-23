"""Logging configuration.

This runs before the rest of the config is loaded (so that config errors
can be logged), which is why it reads the [logging] section on its own.
"""

import configparser
import logging
from pathlib import Path

from .config import DEFAULT_SETTINGS_PATH, VALID_LOG_LEVELS

LOGGER_NAME = "backupd"
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def _logging_settings(settings_path):
    parser = configparser.ConfigParser()
    parser.read(settings_path)
    level = parser.get("logging", "log_level", fallback="INFO").strip().upper()
    if level not in VALID_LOG_LEVELS:
        level = "INFO"
    log_file = parser.get("logging", "log_file", fallback="").strip()
    return level, (Path(log_file).expanduser() if log_file else None)


def configure_logging(settings_path=DEFAULT_SETTINGS_PATH) -> logging.Logger:
    """Configure and return the ``backupd`` logger."""
    level, log_file = _logging_settings(settings_path)
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.setLevel(level)
    formatter = logging.Formatter(FORMAT)

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    logger.addHandler(stream)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    logger.propagate = False
    return logger
