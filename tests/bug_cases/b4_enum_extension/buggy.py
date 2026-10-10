def describe(status):
    """Human-readable label for a Status."""
    if status.name == "PENDING":
        return "waiting to start"
    if status.name == "RUNNING":
        return "in progress"
    return "unknown"
