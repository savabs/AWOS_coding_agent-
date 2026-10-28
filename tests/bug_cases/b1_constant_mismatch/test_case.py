from buggy import retry_budget
from limits import MAX_RETRIES

def test_uses_project_limit():
    assert retry_budget(0) == MAX_RETRIES

def test_decrements():
    assert retry_budget(2) == MAX_RETRIES - 2

def test_exhausted_at_limit():
    assert retry_budget(MAX_RETRIES) == 0
