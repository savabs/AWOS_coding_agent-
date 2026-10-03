"""Logging configuration.

This runs before the rest of the config is loaded (so that config errors
can be logged), which is why it resolves only the logging settings
(APP_LOG_LEVEL / APP_LOG_FILE, falling back to the [logging] section).
"""

import logging

from .config import DEFAULT_SETTINGS_PATH, VALID_LOG_LEVELS, read_parser, resolve_setting

LOGGER_NAME = "backupd"
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def _logging_settings(settings_path):
    parser = read_parser(settings_path)
    level = resolve_setting("log_level", parser=parser).upper()
    if level not in VALID_LOG_LEVELS:
        level = "INFO"
    return level, resolve_setting("log_file", parser=parser)


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
