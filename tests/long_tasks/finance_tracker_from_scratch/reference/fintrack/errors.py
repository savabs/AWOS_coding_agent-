"""Exception hierarchy for fintrack."""


class FintrackError(Exception):
    """Base class for all fintrack errors."""


class ParseError(FintrackError):
    """A whole file cannot be imported."""


class RulesError(FintrackError):
    """The rules file is invalid."""


class ValidationError(FintrackError):
    """A bad argument (month, amount, date range, format, ...)."""
