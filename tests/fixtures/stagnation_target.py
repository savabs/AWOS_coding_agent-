"""Intentionally broken file to trigger stagnation breaker."""


def broken_function()
    """This function is missing a colon - worker will struggle to fix it."""
    return "hello"


def working_function():
    """This one is fine."""
    return 42
