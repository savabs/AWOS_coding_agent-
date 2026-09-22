"""Domain models."""

from dataclasses import dataclass


@dataclass
class User:
    id: int
    # Renamed from `username` in the 2.0 schema change; the API still
    # exposes it as "name".
    display_name: str
