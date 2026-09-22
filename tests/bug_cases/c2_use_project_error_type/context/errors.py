"""Project error hierarchy. Handlers catch these by type."""


class AppError(Exception):
    """Base for every error this application raises deliberately."""


class ValidationError(AppError):
    """Input failed validation. Carries the offending field name."""

    def __init__(self, message, field=None):
        super().__init__(message)
        self.field = field
