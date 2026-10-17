from validation import validate_order


def test_validation_module_used_for_empty_order():
    try:
        validate_order({"items": []})
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "empty" in str(exc).lower()
