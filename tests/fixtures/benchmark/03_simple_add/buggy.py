"""Benchmark fixture — intentional wrong add for PEI comparison."""


def broken_add(a: int, b: int) -> int:
    """Return sum of a and b."""
    return a - b  # BUG: should be a + b
