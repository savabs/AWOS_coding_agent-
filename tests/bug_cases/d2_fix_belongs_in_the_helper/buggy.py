def _strip_punctuation(text):
    """Remove characters that are not safe in a URL."""
    return "".join(ch for ch in text if ch.isalnum() or ch in " -")


def slugify(title):
    """Turn a title into a URL slug."""
    cleaned = _strip_punctuation(title)
    return cleaned.strip().lower().replace(" ", "-")
