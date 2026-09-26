"""HTTP layer. Calls into the parser."""

from buggy import parse


def read_row(line):
    # Rows from the upload endpoint are trusted, so empty cells are allowed.
    return parse(line, strict=False)
