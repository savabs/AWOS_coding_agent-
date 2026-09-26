"""Reading order-import files (``ordertool import FILE``).

An import file is a CSV with a header row and the columns ``customer``,
``email``, ``created_at`` and ``items`` (``SKU:QTY:PRICE`` entries separated by
``|``, as in the orders file), plus an optional ``discount`` percentage.
Files saved by Excel (UTF-8 byte-order mark, CRLF line endings) are accepted.
"""

import csv

from .errors import StorageError, ValidationError
from .utils.dates import parse_timestamp

REQUIRED_COLUMNS = ["customer", "email", "created_at", "items"]
OPTIONAL_COLUMNS = ["discount"]


def read_import_file(path):
    """Parse ``path`` into rows for :meth:`OrderService.import_orders`.

    Raises :class:`StorageError` if the file cannot be read and
    :class:`ValidationError` (with the line number) for bad content.
    """
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            columns = [name.strip() for name in (reader.fieldnames or [])]
            missing = [name for name in REQUIRED_COLUMNS if name not in columns]
            if missing:
                raise ValidationError(
                    f"{path}: missing column(s) {', '.join(missing)} "
                    f"(expected {', '.join(REQUIRED_COLUMNS)} and optionally discount)"
                )
            reader.fieldnames = columns
            rows = []
            for record in reader:
                line = reader.line_num
                try:
                    created_at = parse_timestamp(record["created_at"])
                except ValidationError as exc:
                    raise ValidationError(f"line {line}: {exc}") from None
                items_text = (record["items"] or "").strip()
                items = [part for part in items_text.split("|")] if items_text else []
                discount = (record.get("discount") or "").strip() or None
                rows.append((line, record["customer"], record["email"], created_at, items, discount))
            return rows
    except OSError as exc:
        raise StorageError(f"cannot read {path}: {exc.strerror or exc}") from None
