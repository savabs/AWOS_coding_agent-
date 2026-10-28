import re


def _strip_punctuation(text):
    """Remove characters that are not safe in a URL."""
    text = text.replace("_", " ")
    return "".join(ch for ch in text if ch.isalnum() or ch in " -")


def slugify(title):
    """Turn a title into a URL slug."""
    cleaned = _strip_punctuation(title)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip().lower().replace(" ", "-")
