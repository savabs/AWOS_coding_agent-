"""Byte sizes: how backupd prints and parses them.

Units are binary (1 KiB = 1024 bytes).  Every size shown to the user goes
through :func:`format_size` so that all commands print sizes the same way.
"""

import re

_UNITS = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgtp]?)(?:i?b)?\s*$", re.IGNORECASE)
_FACTORS = {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3, "t": 1024 ** 4, "p": 1024 ** 5}


def format_size(num_bytes: int) -> str:
    """``512 -> '512 B'``, ``1536 -> '1.5 KiB'``, ``10 * 1024**2 -> '10.0 MiB'``."""
    size = float(num_bytes)
    if abs(size) < 1024:
        return f"{int(num_bytes)} B"
    for unit in _UNITS[1:]:
        size /= 1024
        if abs(size) < 1024 or unit == _UNITS[-1]:
            return f"{size:.1f} {unit}"
    raise AssertionError("unreachable")


def parse_size(raw: str) -> int:
    """``'500M' -> 524288000``; accepts bytes or K/M/G/T/P with optional B/iB.

    Raises ValueError for anything else.
    """
    match = _SIZE_RE.match(str(raw))
    if not match:
        raise ValueError(f"not a size: {raw!r}")
    number, unit = match.groups()
    return int(float(number) * _FACTORS[unit.lower()])
