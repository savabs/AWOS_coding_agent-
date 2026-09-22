from errors import ValidationError


def check_age(age):
    """Validate an age, raising on bad input."""
    if age < 0:
        raise ValidationError("age must not be negative", field="age")
    return age
