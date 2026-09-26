def parse(text, strict=False):
    """Split a comma-separated line into fields."""
    fields = [part.strip() for part in text.split(",")]
    if strict and any(f == "" for f in fields):
        raise ValueError("empty field in strict mode")
    return fields
