"""Customer settings: validation and lookup on top of storage."""

from __future__ import annotations

from typing import List

from .currency import SYMBOLS
from .models import Customer
from .storage import Storage
from .timeutil import get_zone

# Legacy abbreviations some older merchant records still carry.
ZONE_ALIASES = {
    "PST": "America/Los_Angeles",
    "PDT": "America/Los_Angeles",
    "EST": "America/New_York",
    "IST": "Asia/Kolkata",
    "NZT": "Pacific/Auckland",
    "GMT": "UTC",
    "Z": "UTC",
}


class UnknownCustomer(LookupError):
    pass


def normalise_timezone(name: str) -> str:
    """Map legacy aliases to IANA names and validate the result."""
    name = (name or "UTC").strip()
    name = ZONE_ALIASES.get(name.upper(), name)
    get_zone(name)  # raises ValueError for unknown zones
    return name


class SettingsStore:
    """Validated access to customer settings."""

    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    def register(self, customer_id: str, name: str, timezone: str = "UTC",
                 currency: str = "USD") -> Customer:
        if not customer_id:
            raise ValueError("customer_id is required")
        currency = currency.upper()
        if currency not in SYMBOLS:
            raise ValueError(f"unsupported currency: {currency}")
        customer = Customer(customer_id, name, normalise_timezone(timezone), currency)
        self.storage.upsert_customer(customer)
        return customer

    def get(self, customer_id: str) -> Customer:
        customer = self.storage.get_customer(customer_id)
        if customer is None:
            raise UnknownCustomer(customer_id)
        return customer

    def set_timezone(self, customer_id: str, timezone: str) -> Customer:
        current = self.get(customer_id)
        updated = Customer(current.customer_id, current.name,
                           normalise_timezone(timezone), current.currency)
        self.storage.upsert_customer(updated)
        return updated

    def all(self) -> List[Customer]:
        return self.storage.list_customers()
