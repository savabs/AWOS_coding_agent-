import pytest
from buggy import parse
from api import read_row

def test_default_behaviour_unchanged():
    assert parse("a, b, c") == ["a", "b", "c"]

def test_accepts_the_keyword_its_caller_passes():
    assert parse("a,,c", strict=False) == ["a", "", "c"]

def test_strict_rejects_empty_field():
    with pytest.raises(ValueError):
        parse("a,,c", strict=True)

def test_caller_works_end_to_end():
    assert read_row("x, y") == ["x", "y"]
