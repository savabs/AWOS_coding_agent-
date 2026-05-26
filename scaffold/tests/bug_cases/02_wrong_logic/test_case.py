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
    c.set("d", 4)          # should evict "a"
    assert c.get("a") is None
    assert c.get("b") == 2
    assert c.get("d") == 4
    assert c.size() == 3


def test_cache_no_eviction_under_limit():
    c = Cache(max_size=5)
    for i in range(5):
        c.set(str(i), i)
    assert c.size() == 5


def test_cache_update_existing_key_no_evict():
    c = Cache(max_size=2)
    c.set("x", 1)
    c.set("y", 2)
    c.set("x", 99)         # update, not insert — should not evict
    assert c.size() == 2
    assert c.get("x") == 99
    assert c.get("y") == 2


def test_palindrome_simple():
    assert is_palindrome("racecar") is True


def test_palindrome_with_spaces():
    assert is_palindrome("a man a plan a canal panama") is True


def test_not_palindrome():
    assert is_palindrome("hello") is False


def test_palindrome_case_insensitive():
    assert is_palindrome("Madam") is True
