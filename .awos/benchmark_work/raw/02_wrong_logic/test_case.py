import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("buggy_02", Path(__file__).parent / "buggy.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
Cache = _mod.Cache
is_palindrome = _mod.is_palindrome


def test_cache_evicts_oldest_not_newest():
    c = Cache(max_size=3)
    c.set("a", 1)
    c.set("b", 2)
    c.set("c", 3)
    c.set("d", 4)
    assert c.get("a") is None
    assert c.get("b") == 2
    assert c.get("d") == 4


def test_palindrome_simple():
    assert is_palindrome("racecar") is True


def test_not_palindrome():
    assert is_palindrome("hello") is False
