def describe(status):
    """Human-readable label for a Status."""
    labels = {
        "PENDING": "waiting to start",
        "RUNNING": "in progress",
        "SUCCEEDED": "finished successfully",
        "FAILED": "finished with errors",
        "CANCELLED": "cancelled by user",
    }
    return labels.get(status.name, "unknown")
