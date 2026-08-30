from users import _display_name, format_name


def test_internal_helper_strips_whitespace():
    assert _display_name({"name": " Ada "}) == "Ada"


def test_public_function_uses_display_name():
    assert format_name({"name": " Ada "}) == "Ada"
