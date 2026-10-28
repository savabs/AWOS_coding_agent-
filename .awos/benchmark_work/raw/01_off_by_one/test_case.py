import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("buggy_01", Path(__file__).parent / "buggy.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
find_max = _mod.find_max
sum_range = _mod.sum_range


def test_find_max_last_element_is_largest():
    assert find_max([1, 2, 3, 10]) == 10


def test_find_max_single_element():
    assert find_max([42]) == 42


def test_sum_range_basic():
    assert sum_range(1, 5) == 15
