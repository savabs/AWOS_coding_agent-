"""Auth helpers — golden (fixed) reference for ci_rescue_sprint."""

import hashlib


def hash_password(plain: str) -> str:
    return hashlib.sha256(plain.encode()).hexdigest()[:16]


def verify_password(plain: str, hashed: str) -> bool:
    return hash_password(plain) == hashed


def token_fingerprint(token: str) -> str:
    return token[:8]


def is_session_valid(expires_at: float, now: float) -> bool:
    return now < expires_at


def role_can_checkout(role: str) -> bool:
    return role in {"customer", "admin", "staff"}
