from buggy import slugify

def test_simple_title():
    assert slugify("Hello World") == "hello-world"

def test_drops_punctuation():
    assert slugify("Hello, World!") == "hello-world"

def test_collapses_repeated_spaces():
    assert slugify("Hello   World") == "hello-world"

def test_underscores_become_hyphens():
    assert slugify("hello_world") == "hello-world"

def test_no_leading_or_trailing_hyphen():
    assert slugify("  Hello  ") == "hello"
