from auth import hash_password, is_session_valid, role_can_checkout, token_fingerprint, verify_password


def test_hash_password_deterministic():
    assert hash_password("secret") == hash_password("secret")


def test_verify_password_ok():
    hashed = hash_password("secret")
    assert verify_password("secret", hashed) is True


def test_verify_password_rejects_wrong():
    hashed = hash_password("secret")
    assert verify_password("wrong", hashed) is False


def test_token_fingerprint_short():
    assert token_fingerprint("abcdefgh123456") == "abcdefgh"


def test_session_valid_before_expiry():
    assert is_session_valid(expires_at=1000.0, now=500.0) is True


def test_session_invalid_after_expiry():
    assert is_session_valid(expires_at=100.0, now=200.0) is False


def test_staff_can_checkout():
    assert role_can_checkout("staff") is True
