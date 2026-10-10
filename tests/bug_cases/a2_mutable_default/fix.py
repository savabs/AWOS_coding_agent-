def collect(value, into=None):
    """Append value to a list and return it."""
    if into is None:
        into = []
    into.append(value)
    return into
