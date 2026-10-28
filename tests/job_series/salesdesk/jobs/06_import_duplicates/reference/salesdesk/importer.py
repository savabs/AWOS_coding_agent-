"""CSV order import.

Expected header: ``order_id,customer_id,amount,created_at[,status]``.
``created_at`` must be ISO-8601; an offset or ``Z`` is recommended. Naive
timestamps are treated as UTC, matching the storage convention.

Bad rows never stop an import: each one is skipped and reported as
``line N: <reason>`` (the header is line 1) and the good rows are imported.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, TextIO

from .currency import parse_amount
from .models import Order
from .storage import Storage

REQUIRED = ("order_id", "customer_id", "amount", "created_at")


@dataclass
class ImportResult:
    imported: int = 0
    orders: List[Order] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    # line number of each entry in ``orders`` (the header is line 1)
    lines: List[int] = field(default_factory=list)


def parse_timestamp(text: str) -> datetime:
    value = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def read_orders(handle: TextIO) -> ImportResult:
    reader = csv.DictReader(handle)
    missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"missing columns: {', '.join(missing)}")
    result = ImportResult()
    for line_no, row in enumerate(reader, start=2):
        try:
            order = Order(
                order_id=row["order_id"].strip(),
                customer_id=row["customer_id"].strip(),
                amount_cents=parse_amount(row["amount"]),
                created_at=parse_timestamp(row["created_at"]),
                status=(row.get("status") or "paid").strip() or "paid",
            )
        except (ValueError, TypeError) as exc:
            result.errors.append(f"line {line_no}: {exc}")
            continue
        result.orders.append(order)
        result.lines.append(line_no)
    return result


def _line_of(error: str) -> int:
    return int(error.split(":", 1)[0].split()[1])


def import_orders(storage: Storage, handle: TextIO) -> ImportResult:
    result = read_orders(handle)
    known = {c.customer_id for c in storage.list_customers()}
    seen = set()
    keep, kept_lines = [], []
    for line_no, order in zip(result.lines, result.orders):
        if order.customer_id not in known:
            result.errors.append(f"line {line_no}: unknown customer {order.customer_id!r}")
        elif order.order_id in seen or storage.get_order(order.order_id) is not None:
            result.errors.append(f"line {line_no}: duplicate order_id {order.order_id!r}")
        else:
            seen.add(order.order_id)
            keep.append(order)
            kept_lines.append(line_no)
    result.orders, result.lines = keep, kept_lines
    result.errors.sort(key=_line_of)
    result.imported = storage.add_orders(result.orders)
    return result
