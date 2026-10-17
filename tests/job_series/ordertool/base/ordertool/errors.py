"""Exception hierarchy for ordertool.

Every error that should be reported to a user (rather than crash with a
traceback) derives from :class:`OrderToolError`. The CLI catches this base
class and prints ``error: <message>`` to stderr.
"""


class OrderToolError(Exception):
    """Base class for all user-facing ordertool errors."""


class ValidationError(OrderToolError):
    """Raised when user input (an argument, a field value) is invalid."""


class NotFoundError(OrderToolError):
    """Raised when an order id does not exist in the store."""


class InvalidTransitionError(OrderToolError):
    """Raised when an order status change is not allowed."""


class StorageError(OrderToolError):
    """Raised when the backing file cannot be read or is corrupt."""
