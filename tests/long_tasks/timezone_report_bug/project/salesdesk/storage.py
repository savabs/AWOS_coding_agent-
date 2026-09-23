"""sqlite persistence for customers and orders.

Timestamps are stored as naive-UTC ISO strings (see :mod:`salesdesk.timeutil`).
Orders come back with naive UTC ``created_at`` values.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Iterable, List, Optional

from .models import Customer, Order
from .timeutil import from_storage, to_storage

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    customer_id TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    timezone    TEXT NOT NULL DEFAULT 'UTC',
    currency    TEXT NOT NULL DEFAULT 'USD'
);
CREATE TABLE IF NOT EXISTS orders (
    order_id     TEXT PRIMARY KEY,
    customer_id  TEXT NOT NULL REFERENCES customers(customer_id),
    amount_cents INTEGER NOT NULL,
    created_at   TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'paid'
);
CREATE INDEX IF NOT EXISTS idx_orders_customer_time
    ON orders(customer_id, created_at);
"""


class Storage:
    """Thin repository over a sqlite database."""

    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- customers -------------------------------------------------------
    def upsert_customer(self, customer: Customer) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO customers(customer_id, name, timezone, currency) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(customer_id) DO UPDATE SET "
                "name=excluded.name, timezone=excluded.timezone, "
                "currency=excluded.currency",
                (customer.customer_id, customer.name, customer.timezone, customer.currency),
            )

    def get_customer(self, customer_id: str) -> Optional[Customer]:
        row = self._conn.execute(
            "SELECT * FROM customers WHERE customer_id = ?", (customer_id,)
        ).fetchone()
        if row is None:
            return None
        return Customer(row["customer_id"], row["name"], row["timezone"], row["currency"])

    def list_customers(self) -> List[Customer]:
        rows = self._conn.execute("SELECT * FROM customers ORDER BY customer_id").fetchall()
        return [Customer(r["customer_id"], r["name"], r["timezone"], r["currency"]) for r in rows]

    # -- orders ----------------------------------------------------------
    def add_order(self, order: Order) -> None:
        self.add_orders([order])

    def add_orders(self, orders: Iterable[Order]) -> int:
        rows = [
            (o.order_id, o.customer_id, o.amount_cents, to_storage(o.created_at), o.status)
            for o in orders
        ]
        with self._conn:
            self._conn.executemany(
                "INSERT INTO orders(order_id, customer_id, amount_cents, created_at, status) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def orders_between(self, customer_id: str, start: datetime, end: datetime) -> List[Order]:
        """Orders with ``start <= created_at < end``.

        ``start``/``end`` may be naive UTC or aware; they are normalised to the
        storage form before comparison.
        """
        rows = self._conn.execute(
            "SELECT * FROM orders WHERE customer_id = ? "
            "AND created_at >= ? AND created_at < ? ORDER BY created_at, order_id",
            (customer_id, to_storage(start), to_storage(end)),
        ).fetchall()
        return [self._row_to_order(r) for r in rows]

    def all_orders(self, customer_id: str) -> List[Order]:
        rows = self._conn.execute(
            "SELECT * FROM orders WHERE customer_id = ? ORDER BY created_at, order_id",
            (customer_id,),
        ).fetchall()
        return [self._row_to_order(r) for r in rows]

    def update_status(self, order_id: str, status: str) -> None:
        with self._conn:
            cur = self._conn.execute(
                "UPDATE orders SET status = ? WHERE order_id = ?", (status, order_id)
            )
        if cur.rowcount == 0:
            raise KeyError(order_id)

    @staticmethod
    def _row_to_order(row: sqlite3.Row) -> Order:
        return Order(
            order_id=row["order_id"],
            customer_id=row["customer_id"],
            amount_cents=row["amount_cents"],
            created_at=from_storage(row["created_at"]),
            status=row["status"],
        )
