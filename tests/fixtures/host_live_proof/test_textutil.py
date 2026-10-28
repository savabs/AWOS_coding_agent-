from textutil import slugify


def test_basic():
    assert slugify("Hello World") == "hello-world"


def test_collapses_spaces_and_trims():
    assert slugify("  Hello   World  ") == "hello-world"


def test_drops_punctuation():
    assert slugify("Hello, World!") == "hello-world"
