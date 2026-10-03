import pytest
from buggy import average

def test_normal_case():
    assert average([1, 2, 3]) == 2.0

def test_empty_returns_zero():
    assert average([]) == 0.0

def test_empty_does_not_raise():
    try:
        average([])
    except ZeroDivisionError:
        pytest.fail("average([]) must not raise ZeroDivisionError")

def test_single_element():
    assert average([7]) == 7.0
