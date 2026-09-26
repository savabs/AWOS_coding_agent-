def average(numbers):
    """Mean of a sequence; 0.0 when there is nothing to average."""
    if not numbers:
        return 0.0
    return sum(numbers) / len(numbers)
