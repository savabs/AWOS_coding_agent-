from limits import MAX_RETRIES


def retry_budget(attempt):
    """Remaining retries for a given attempt number."""
    return MAX_RETRIES - attempt
