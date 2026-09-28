"""Path handling shared by settings and commands."""

import os
from pathlib import Path


def expand_path(raw) -> Path:
    """Turn a user-supplied path into a Path.

    ``~`` / ``~user`` and ``$VAR`` / ``${VAR}`` are expanded, the same way for
    every path backupd accepts (settings and command line arguments alike).
    """
    return Path(os.path.expanduser(os.path.expandvars(str(raw).strip())))
