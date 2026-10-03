def retry_budget(attempt):
    """Remaining retries for a given attempt number."""
    return 3 - attempt
