from buggy import describe
from status import Status

def test_known_states():
    assert describe(Status.PENDING) == "waiting to start"
    assert describe(Status.RUNNING) == "in progress"

def test_every_status_has_a_label():
    for member in Status:
        assert describe(member) != "unknown", f"no label for {member.name}"

def test_cancelled_label():
    assert describe(Status.CANCELLED) == "cancelled by user"
