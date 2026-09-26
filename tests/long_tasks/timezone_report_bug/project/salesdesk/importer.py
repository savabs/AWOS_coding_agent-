"""CSV order import.

Expected header: ``order_id,customer_id,amount,created_at[,status]``.
``created_at`` must be ISO-8601; an offset or ``Z`` is recommended. Naive
timestamps are treated as UTC, matching the storage convention.
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
    return result


def import_orders(storage: Storage, handle: TextIO) -> ImportResult:
    result = read_orders(handle)
    result.imported = storage.add_orders(result.orders)
    return result
