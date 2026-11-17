"""Demo authentication: pbkdf2 password hashing + login helpers.

Stdlib-only (``hashlib.pbkdf2_hmac``) — no extra dependency. Passwords are
stored as ``salt_hex$hash_hex``. Two roles exist: ``manager`` and ``employee``.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from .repository import User

ROLE_MANAGER = "manager"
ROLE_EMPLOYEE = "employee"
ROLES = (ROLE_MANAGER, ROLE_EMPLOYEE)

#: The company's departments. Every member account carries one, and every
#: order they create is auto-assigned from it.
DEPARTMENTS = (
    "Engineering",
    "Accounting",
    "Human Resources",
    "Marketing",
    "Operations",
    "Legal",
    "Procurement",
)

_ITERATIONS = 240_000


def hash_password(password: str) -> str:
    """Hash a password with a random salt: ``salt_hex$hash_hex``."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, hash_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return hmac.compare_digest(actual, expected)


def login(session, username: str, password: str) -> User | None:
    """Return the active user if credentials match, else ``None``."""
    user = session.query(User).filter_by(username=username.strip()).one_or_none()
    if user is None or not user.active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def create_user(
    session,
    *,
    username: str,
    display_name: str,
    role: str,
    password: str,
    department: str | None = None,
) -> User:
    """Create a user. Raises ``ValueError`` on duplicate username or bad role."""
    from datetime import datetime, timezone

    username = username.strip()
    if not username:
        raise ValueError("username cannot be empty")
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    if (
        session.query(User).filter_by(username=username).one_or_none() is not None
    ):
        raise ValueError(f"username {username!r} already exists")

    user = User(
        username=username,
        display_name=display_name.strip() or username,
        role=role,
        department=(department or "").strip() or None,
        password_hash=hash_password(password),
        active=True,
        created_at=datetime.now(timezone.utc),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user