"""Intentional bug fixture for G3 failure-hunt gauntlet. Do not use in production paths."""


def broken_add(a: int, b: int) -> int:
    """Return sum of a and b — currently wrong for gauntlet stress tests."""
    return a + b  # fixed by gauntlet G3 MCTS path
