from buggy import midpoint

def test_returns_int():
    assert isinstance(midpoint(0, 10), int)

def test_even_span():
    assert midpoint(0, 10) == 5

def test_odd_span_floors():
    assert midpoint(0, 9) == 4

def test_usable_as_index():
    assert [10, 20, 30, 40, 50][midpoint(0, 4)] == 30
