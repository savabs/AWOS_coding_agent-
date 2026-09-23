"""Date and time helpers.

Order timestamps are naive local datetimes stored in ISO-8601 form with
second precision, e.g. ``2024-03-10T14:05:00``.
"""

from datetime import datetime, timedelta

from ..errors import ValidationError

TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S"
DATE_FORMAT = "%Y-%m-%d"


def now():
    """Return the current local time truncated to whole seconds."""
    return datetime.now().replace(microsecond=0)


def format_timestamp(value):
    """Render a datetime in the storage format (``YYYY-MM-DDTHH:MM:SS``)."""
    return value.strftime(TIMESTAMP_FORMAT)


def parse_timestamp(text):
    """Parse a stored timestamp string back into a datetime.

    Accepts the full storage format and, for convenience when importing
    hand-written data, a bare ``YYYY-MM-DD`` date (interpreted as midnight).

    Raises :class:`ValidationError` if the text is not a valid timestamp.
    """
    text = (text or "").strip()
    for fmt in (TIMESTAMP_FORMAT, DATE_FORMAT):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValidationError(f"invalid timestamp {text!r}")


def parse_date_arg(text):
    """Parse a user-supplied calendar date in ``YYYY-MM-DD`` form.

    Returns a :class:`datetime.date`. Raises :class:`ValidationError` with a
    message naming the bad value and the expected format.
    """
    raw = (text or "").strip()
    try:
        return datetime.strptime(raw, DATE_FORMAT).date()
    except ValueError:
        raise ValidationError(
            f"invalid date {text!r}: expected YYYY-MM-DD (e.g. 2024-03-31)"
        ) from None


def days_ago(days, reference=None):
    """Return the datetime ``days`` days before ``reference`` (default: now)."""
    reference = reference or now()
    return reference - timedelta(days=days)


def format_day(value):
    """Render only the calendar date of a datetime (``YYYY-MM-DD``)."""
    return value.strftime(DATE_FORMAT)
