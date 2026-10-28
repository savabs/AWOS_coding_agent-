from buggy import paginate

def test_first_page_is_full():
    assert paginate(list(range(10)), 1, 3) == [0, 1, 2]

def test_second_page_is_full():
    assert paginate(list(range(10)), 2, 3) == [3, 4, 5]

def test_last_partial_page():
    assert paginate(list(range(10)), 4, 3) == [9]

def test_page_beyond_end_is_empty():
    assert paginate(list(range(10)), 9, 3) == []
