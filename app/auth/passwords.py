"""Password hashing (argon2id) + a backend-enforced complexity policy.

The same ``validate_password`` runs in the admin CLI and the HTTP password-change endpoint, so the
policy is never merely frontend-gated (spec: backend-enforced).
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_ph = PasswordHasher()
MIN_LENGTH = 12


class WeakPassword(ValueError):
    """Raised when a candidate password fails the complexity policy."""


def validate_password(password: str) -> None:
    """Enforce: >= 12 chars and at least 3 of {lowercase, uppercase, digit, symbol}."""
    if len(password) < MIN_LENGTH:
        raise WeakPassword(f"password must be at least {MIN_LENGTH} characters")
    classes = sum(
        (
            any(c.islower() for c in password),
            any(c.isupper() for c in password),
            any(c.isdigit() for c in password),
            any(not c.isalnum() for c in password),
        )
    )
    if classes < 3:
        raise WeakPassword(
            "password must include at least 3 of: lowercase, uppercase, digit, symbol"
        )


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        _ph.verify(password_hash, password)
        return True
    except (VerificationError, InvalidHashError):  # VerifyMismatchError is a VerificationError
        return False
