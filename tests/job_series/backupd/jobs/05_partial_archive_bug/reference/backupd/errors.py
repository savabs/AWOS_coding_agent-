"""Errors backupd reports to the user.

Every problem a user can cause or fix (bad settings, missing directories,
unreadable archives, ...) is raised as a subclass of :class:`BackupdError`.
``backupd.cli.main`` turns them into one line on stderr and an exit status,
never a traceback:

* :class:`ConfigError`   -> ``configuration error: <message>``, exit 2
* any other BackupdError -> ``error: <message>``, exit 1

Messages should name the setting or the path involved.
"""


class BackupdError(Exception):
    """Base class for errors that are reported to the user without a traceback."""


class ConfigError(BackupdError):
    """Raised when the configuration is missing or invalid."""


class SourceError(BackupdError):
    """The directory to back up is missing or unusable."""


class SpaceError(BackupdError):
    """Not enough free space where the backups are written."""


class ArchiveError(BackupdError):
    """An archive could not be written or read."""
