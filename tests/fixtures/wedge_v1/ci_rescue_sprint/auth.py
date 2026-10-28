"""Auth helpers — ci_rescue_sprint fixture (intentionally buggy)."""

import hashlib


def hash_password(plain: str) -> str:
    return hashlib.sha256(plain.encode()).hexdigest()[:16]


def verify_password(plain: str, hashed: str) -> bool:
    # Bug: compares plain to hash incorrectly
    return hash_password(plain) == plain


def token_fingerprint(token: str) -> str:
    # Bug: returns full token instead of short fingerprint
    return token


def is_session_valid(expires_at: float, now: float) -> bool:
    # Bug: inverted expiry check
    return now > expires_at


def role_can_checkout(role: str) -> bool:
    allowed = {"customer", "admin", "staff"}
    # Bug: blocks staff
    return role in allowed and role != "staff"
