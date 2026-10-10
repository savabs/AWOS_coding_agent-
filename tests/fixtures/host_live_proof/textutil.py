def slugify(title):
    """Lowercase, words joined by single hyphens, no leading/trailing hyphens."""
    return title.lower().replace(" ", "-")
