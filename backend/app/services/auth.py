"""Internal user authentication primitives."""
import hashlib
import hmac
import os
import re
import secrets
from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.core.time import utc_now
from app.models.users import User, UserSession

PASSWORD_MIN_LENGTH = 8
SESSION_LIFETIME_DAYS = 30
AUTH_DISABLED_ENV = "OSD_AUTH_DISABLED"
ROLES = ("viewer", "operator", "admin")

_USERNAME_PATTERN = re.compile(r"[a-z0-9._-]{1,64}")


def hash_password(password: str) -> str:
    """Return a versioned scrypt password hash with a random salt."""
    salt = secrets.token_bytes(16)
    password_hash = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${password_hash.hex()}"


_DUMMY_PASSWORD_HASH = hash_password("not-a-valid-password")


def verify_password(password: str, stored: str) -> bool:
    """Check a password against a stored versioned scrypt hash."""
    try:
        algorithm, n_text, r_text, p_text, salt_hex, hash_hex = stored.split("$")
        if algorithm != "scrypt":
            return False
        n, r, p = int(n_text), int(r_text), int(p_text)
        if n <= 1 or n & (n - 1) or r < 1 or p < 1:
            return False
        salt = bytes.fromhex(salt_hex)
        expected_hash = bytes.fromhex(hash_hex)
        calculated_hash = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=len(expected_hash))
    except (MemoryError, TypeError, ValueError):
        return False
    return hmac.compare_digest(calculated_hash, expected_hash)


def normalize_username(username: str) -> str:
    """Normalize usernames before validation and lookup."""
    return username.strip().lower()


def validate_new_user(db: Session, username: str, password: str) -> str | None:
    """Return a stable validation error code, if the proposed user is invalid."""
    normalized_username = normalize_username(username)
    if _USERNAME_PATTERN.fullmatch(normalized_username) is None:
        return "invalid_username"
    if db.query(User).filter(User.username == normalized_username).first() is not None:
        return "username_taken"
    if len(password) < PASSWORD_MIN_LENGTH:
        return "password_too_short"
    return None


def create_user(db: Session, username: str, password: str, role: str) -> User:
    """Create a normalized internal user."""
    user = User(
        username=normalize_username(username),
        password_hash=hash_password(password),
        role=role,
    )
    db.add(user)
    db.flush()
    return user


def authenticate(db: Session, username: str, password: str) -> User | None:
    """Authenticate an active user without revealing username existence by timing."""
    user = db.query(User).filter(User.username == normalize_username(username)).first()
    if user is None or not user.is_active:
        verify_password(password, _DUMMY_PASSWORD_HASH)
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def create_session(db: Session, user: User) -> str:
    """Create a revocable database-backed session and return its plaintext token."""
    token = secrets.token_urlsafe(32)
    db.add(
        UserSession(
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            user_id=user.id,
            expires_at=(utc_now() + timedelta(days=SESSION_LIFETIME_DAYS)).replace(tzinfo=None),
        )
    )
    return token


def resolve_session(db: Session, token: str) -> User | None:
    """Return the active user for a valid session token, if any."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    session = db.query(UserSession).filter(UserSession.token_hash == token_hash).first()
    if session is None:
        return None
    if session.expires_at <= utc_now().replace(tzinfo=None):
        db.delete(session)
        return None
    user = db.query(User).filter(User.id == session.user_id).first()
    return user if user is not None and user.is_active else None


def delete_session(db: Session, token: str) -> None:
    """Revoke one session by its plaintext token."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    db.query(UserSession).filter(UserSession.token_hash == token_hash).delete()


def delete_user_sessions(db: Session, user_id: int) -> None:
    """Revoke every session belonging to a user."""
    db.query(UserSession).filter(UserSession.user_id == user_id).delete()


def cleanup_expired_sessions(db: Session) -> None:
    """Remove sessions whose absolute lifetime has ended."""
    db.query(UserSession).filter(UserSession.expires_at <= utc_now().replace(tzinfo=None)).delete()


def auth_enabled(db: Session) -> bool:
    """Return whether internal authentication is enabled for this process."""
    if os.environ.get(AUTH_DISABLED_ENV, "").lower() in ("1", "true", "yes"):
        return False
    return get_setting_value(db, "auth.enabled", "false") == "true"


def active_admin_count(db: Session, exclude_user_id: int | None = None) -> int:
    """Count active administrators, optionally excluding one user."""
    query = db.query(User).filter(User.role == "admin", User.is_active == True)  # noqa: E712
    if exclude_user_id is not None:
        query = query.filter(User.id != exclude_user_id)
    return query.count()
