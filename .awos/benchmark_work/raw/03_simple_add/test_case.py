import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("buggy_03", Path(__file__).parent / "buggy.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
broken_add = _mod.broken_add


def test_add_positive():
    assert broken_add(2, 3) == 5


def test_add_zero():
    assert broken_add(0, 0) == 0
