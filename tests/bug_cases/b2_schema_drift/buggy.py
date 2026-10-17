def serialize(user):
    """Render a User as a dict for the API."""
    return {"id": user.id, "name": user.username}
