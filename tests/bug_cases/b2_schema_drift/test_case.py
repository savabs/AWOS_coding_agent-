from buggy import serialize
from models import User

def test_uses_current_field_name():
    assert serialize(User(id=1, display_name="ada")) == {"id": 1, "name": "ada"}

def test_does_not_touch_removed_field():
    user = User(id=2, display_name="grace")
    assert not hasattr(user, "username")
    serialize(user)
