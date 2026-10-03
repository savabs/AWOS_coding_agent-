from buggy import collect

def test_single_call():
    assert collect(1) == [1]

def test_calls_do_not_share_state():
    assert collect("a") == ["a"]
    assert collect("b") == ["b"]

def test_explicit_list_still_accumulates():
    acc = []
    collect(1, acc)
    collect(2, acc)
    assert acc == [1, 2]
