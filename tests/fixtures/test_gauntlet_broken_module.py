"""Verifier target for G3 failure-hunt gauntlet."""

from tests.fixtures.gauntlet_broken_module import broken_add


def test_broken_add():
    assert broken_add(2, 3) == 5
