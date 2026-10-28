def check_age(age):
    """Validate an age, raising on bad input."""
    if age < 0:
        raise ValueError("age must not be negative")
    return age
