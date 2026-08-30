from calculator import subtract
from __init__ import subtract as exported_subtract


def test_implementation():
    assert subtract(5, 3) == 2


def test_export():
    assert exported_subtract(5, 3) == 2
