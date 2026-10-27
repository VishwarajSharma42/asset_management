"""Argon2id password hashing and a deliberately modest password policy."""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from ..errors import Unprocessable

_hasher = PasswordHasher()  # Argon2id, library defaults (RFC 9106 low-memory profile)
# Verifying against this when the user does not exist keeps response time uniform.
DUMMY_HASH = _hasher.hash("dsoreg-dummy-password-never-valid")

_COMMON = {"password", "password123", "123456789012", "qwertyuiop12", "admin@123456",
           "changeme1234", "welcome12345", "letmein12345"}


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    return _hasher.check_needs_rehash(stored_hash)


def check_policy(password: str, username: str = "") -> None:
    if len(password) < 12:
        raise Unprocessable("Password must be at least 12 characters.", code="weak_password")
    if len(password) > 256:
        raise Unprocessable("Password is too long.", code="weak_password")
    low = password.lower()
    if low in _COMMON or (username and username.lower() in low) or len(set(password)) < 5:
        raise Unprocessable("That password is too easy to guess.", code="weak_password")
