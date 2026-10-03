def paginate(items, page, per_page):
    """Return the items on a 1-indexed page."""
    start = (page - 1) * per_page
    end = start + per_page
    return items[start:end]
