import os
from buggy import resolve

def test_absolute_path_unchanged():
    assert resolve("/tmp/x") == "/tmp/x"

def test_expands_home():
    expected = os.path.join(os.path.expanduser("~"), "notes.txt")
    assert resolve("~/notes.txt") == expected

def test_collapses_dot_segments():
    assert resolve("/tmp/a/../b") == "/tmp/b"
