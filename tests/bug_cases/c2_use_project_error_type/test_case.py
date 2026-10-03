import pytest
from buggy import check_age
from errors import ValidationError

def test_valid_age_passes_through():
    assert check_age(30) == 30

def test_raises_project_error_type():
    with pytest.raises(ValidationError):
        check_age(-1)

def test_error_carries_the_field_name():
    with pytest.raises(ValidationError) as excinfo:
        check_age(-1)
    assert excinfo.value.field == "age"
