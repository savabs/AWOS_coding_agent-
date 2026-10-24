"""Job lifecycle states."""

from enum import Enum


class Status(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    # Added when the cancel endpoint shipped.
    CANCELLED = "cancelled"
