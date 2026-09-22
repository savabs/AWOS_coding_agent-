def parse(text):
    """Split a comma-separated line into fields."""
    return [part.strip() for part in text.split(",")]
