"""Internal user authentication primitives."""
import hashlib
import hmac
import ipaddress
import os
import re
import secrets
from datetime import timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.core.time import utc_now
from app.models.users import ExternalIdentity, User, UserSession
from app.services.saved_views import copy_legacy_views_to_user
from app.services.user_preferences import create_user_preferences

PASSWORD_MIN_LENGTH = 8
SESSION_LIFETIME_DAYS = 30
AUTH_DISABLED_ENV = "OSD_AUTH_DISABLED"
AUTH_HOSTNAME_SETTING = "auth.hostname"
ROLES = ("viewer", "operator", "admin")

AUTH_METHOD_PASSWORD = "password"
AUTH_METHOD_OIDC = "oidc"
AUTH_METHODS = (AUTH_METHOD_PASSWORD, AUTH_METHOD_OIDC)

IDENTITY_PROVIDER_OIDC = "oidc"

# Usernames for automatically created external users. The hash is derived from
# issuer and subject so the same provider account always produces the same
# name, without putting a subject or an address into the username.
JIT_USERNAME_PREFIX = "oidc-"
JIT_USERNAME_HASH_LENGTH = 12
JIT_USERNAME_ATTEMPTS = 5

# OWASP lists this 16 MiB / five-pass profile as equivalent to its 128 MiB
# scrypt minimum while keeping five concurrent logins within a 512 MB container.
SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 5
SCRYPT_DKLEN = 32
SCRYPT_MAXMEM = 32 * 1024 * 1024
_LEGACY_SCRYPT_COST = (16384, 8, 1)
_CURRENT_SCRYPT_COST = (SCRYPT_N, SCRYPT_R, SCRYPT_P)
_SUPPORTED_SCRYPT_COSTS = {_LEGACY_SCRYPT_COST, _CURRENT_SCRYPT_COST}

_USERNAME_PATTERN = re.compile(r"[a-z0-9._-]{1,64}")
_HOSTNAME_LABEL_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def hash_password(password: str) -> str:
    """Return a versioned scrypt password hash with a random salt."""
    salt = secrets.token_bytes(16)
    password_hash = hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
        maxmem=SCRYPT_MAXMEM,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${password_hash.hex()}"


_DUMMY_PASSWORD_HASH = hash_password("not-a-valid-password")


def verify_password(password: str, stored: str | None) -> bool:
    """Check a password against a stored versioned scrypt hash."""
    if stored is None:
        return False
    try:
        algorithm, n_text, r_text, p_text, salt_hex, hash_hex = stored.split("$")
        if algorithm != "scrypt":
            return False
        n, r, p = int(n_text), int(r_text), int(p_text)
        if (n, r, p) not in _SUPPORTED_SCRYPT_COSTS:
            return False
        salt = bytes.fromhex(salt_hex)
        expected_hash = bytes.fromhex(hash_hex)
        if len(salt) != 16 or len(expected_hash) != SCRYPT_DKLEN:
            return False
        calculated_hash = hashlib.scrypt(
            password.encode(),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=SCRYPT_DKLEN,
            maxmem=SCRYPT_MAXMEM,
        )
    except (MemoryError, TypeError, ValueError):
        return False
    return hmac.compare_digest(calculated_hash, expected_hash)


def password_needs_rehash(stored: str | None) -> bool:
    """Return whether a valid legacy hash should use the current cost profile."""
    if stored is None:
        return False
    try:
        algorithm, n_text, r_text, p_text, _salt_hex, _hash_hex = stored.split("$")
        return algorithm == "scrypt" and (int(n_text), int(r_text), int(p_text)) == _LEGACY_SCRYPT_COST
    except ValueError:
        return False


def normalize_username(username: str) -> str:
    """Normalize usernames before validation and lookup."""
    return username.strip().lower()


def normalize_auth_hostname(hostname: str) -> str | None:
    """Return a normalized DNS hostname, or None when the value is invalid."""
    value = hostname.strip().lower()
    if not value or value.endswith("."):
        return None
    try:
        normalized = value.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        pass
    else:
        return None
    if len(normalized) > 253 or any(_HOSTNAME_LABEL_PATTERN.fullmatch(label) is None for label in normalized.split(".")):
        return None
    return normalized


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
    create_user_preferences(db, user.id)
    copy_legacy_views_to_user(db, user.id)
    return user


def authenticate(db: Session, username: str, password: str) -> User | None:
    """Authenticate an active user without revealing username existence by timing."""
    user = db.query(User).filter(User.username == normalize_username(username)).first()
    if user is None or not user.is_active or user.password_hash is None:
        verify_password(password, _DUMMY_PASSWORD_HASH)
        return None
    if not verify_password(password, user.password_hash):
        return None
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        db.commit()
    return user


def create_session(db: Session, user: User, auth_method: str) -> str:
    """Create a revocable database-backed session and return its plaintext token."""
    if auth_method not in AUTH_METHODS:
        raise ValueError(f"Unsupported authentication method: {auth_method}")
    token = secrets.token_urlsafe(32)
    db.add(
        UserSession(
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            user_id=user.id,
            expires_at=(utc_now() + timedelta(days=SESSION_LIFETIME_DAYS)).replace(tzinfo=None),
            auth_method=auth_method,
        )
    )
    return token


def resolve_session_with_method(db: Session, token: str) -> tuple[User, str] | None:
    """Return the active user and the method the session was created with."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    session = db.query(UserSession).filter(UserSession.token_hash == token_hash).first()
    if session is None:
        return None
    if session.expires_at <= utc_now().replace(tzinfo=None):
        db.delete(session)
        return None
    user = db.query(User).filter(User.id == session.user_id).first()
    if user is None or not user.is_active:
        return None
    # An unknown stored method is treated as a password session: it must never
    # pass as the proof that single sign-on works for this administrator.
    return user, session.auth_method if session.auth_method in AUTH_METHODS else AUTH_METHOD_PASSWORD


def resolve_session(db: Session, token: str) -> User | None:
    """Return the active user for a valid session token, if any."""
    resolved = resolve_session_with_method(db, token)
    return resolved[0] if resolved is not None else None


def delete_session(db: Session, token: str) -> None:
    """Revoke one session by its plaintext token."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    db.query(UserSession).filter(UserSession.token_hash == token_hash).delete()


def delete_user_sessions(db: Session, user_id: int) -> None:
    """Revoke every session belonging to a user."""
    db.query(UserSession).filter(UserSession.user_id == user_id).delete()


def delete_sessions_by_auth_method(db: Session, auth_method: str) -> None:
    """Revoke every session that was created with one authentication method."""
    if auth_method not in AUTH_METHODS:
        raise ValueError(f"Unsupported authentication method: {auth_method}")
    db.query(UserSession).filter(UserSession.auth_method == auth_method).delete()


def cleanup_expired_sessions(db: Session) -> None:
    """Remove sessions whose absolute lifetime has ended."""
    db.query(UserSession).filter(UserSession.expires_at <= utc_now().replace(tzinfo=None)).delete()


def auth_enabled(db: Session) -> bool:
    """Return whether internal authentication is enabled for this process."""
    if auth_disabled_by_environment():
        return False
    return get_setting_value(db, "auth.enabled", "false") == "true"


def auth_disabled_by_environment() -> bool:
    """Return whether the break-glass environment override is active."""
    return os.environ.get(AUTH_DISABLED_ENV, "").lower() in ("1", "true", "yes")


def active_admin_count(db: Session, exclude_user_id: int | None = None) -> int:
    """Count active administrators, optionally excluding one user."""
    query = db.query(User).filter(User.role == "admin", User.is_active == True)  # noqa: E712
    if exclude_user_id is not None:
        query = query.filter(User.id != exclude_user_id)
    return query.count()


def create_passwordless_user(db: Session, username: str) -> User:
    """Create a viewer that can only sign in through an external identity."""
    user = User(
        username=normalize_username(username),
        password_hash=None,
        role="viewer",
    )
    db.add(user)
    db.flush()
    create_user_preferences(db, user.id)
    copy_legacy_views_to_user(db, user.id)
    return user


def find_external_identity(
    db: Session,
    issuer: str,
    subject: str,
    provider: str = IDENTITY_PROVIDER_OIDC,
) -> ExternalIdentity | None:
    """Return the identity for an exact issuer and subject pair, if any."""
    return (
        db.query(ExternalIdentity)
        .filter(
            ExternalIdentity.provider == provider,
            ExternalIdentity.issuer == issuer,
            ExternalIdentity.subject == subject,
        )
        .first()
    )


def find_user_external_identity(
    db: Session,
    user_id: int,
    provider: str = IDENTITY_PROVIDER_OIDC,
) -> ExternalIdentity | None:
    """Return the external identity linked to a local user, if any."""
    return (
        db.query(ExternalIdentity)
        .filter(ExternalIdentity.provider == provider, ExternalIdentity.user_id == user_id)
        .first()
    )


def link_external_identity(
    db: Session,
    user_id: int,
    issuer: str,
    subject: str,
    provider: str = IDENTITY_PROVIDER_OIDC,
) -> ExternalIdentity | str:
    """Link an external identity to a local user, or return a validation error code."""
    if find_user_external_identity(db, user_id, provider) is not None:
        return "identity_already_linked"
    existing = find_external_identity(db, issuer, subject, provider)
    if existing is not None:
        return "identity_taken"
    identity = ExternalIdentity(user_id=user_id, provider=provider, issuer=issuer, subject=subject)
    try:
        # A concurrent callback can win the race between the checks above and
        # this insert, so let the database unique constraints decide. The
        # savepoint keeps unrelated pending work in this session usable.
        with db.begin_nested():
            db.add(identity)
            db.flush()
    except IntegrityError:
        return "identity_taken"
    return identity


def unlink_external_identity(db: Session, user_id: int, provider: str = IDENTITY_PROVIDER_OIDC) -> bool:
    """Remove a user's external identity and report whether one was removed."""
    return bool(
        db.query(ExternalIdentity)
        .filter(ExternalIdentity.provider == provider, ExternalIdentity.user_id == user_id)
        .delete()
    )


def delete_user_external_identities(db: Session, user_id: int) -> None:
    """Remove every external identity belonging to a user."""
    db.query(ExternalIdentity).filter(ExternalIdentity.user_id == user_id).delete()


def jit_fallback_username(issuer: str, subject: str, attempt: int = 0) -> str:
    """Return a stable, data-minimal username for one external identity."""
    digest = hashlib.sha256(f"{issuer}\x00{subject}".encode()).hexdigest()
    suffix = f"-{attempt + 1}" if attempt else ""
    return f"{JIT_USERNAME_PREFIX}{digest[:JIT_USERNAME_HASH_LENGTH]}{suffix}"


def _jit_usernames(db: Session, issuer: str, subject: str, preferred_username: object):
    """Yield the usernames to try for a new external user, best first."""
    candidate = normalize_username(preferred_username) if isinstance(preferred_username, str) else ""
    # A provider name is only ever a convenience: it has to satisfy the local
    # username contract and be free, and it never matches an existing account
    # onto the external identity.
    if _USERNAME_PATTERN.fullmatch(candidate) is not None and db.query(User).filter(User.username == candidate).first() is None:
        yield candidate
    for attempt in range(JIT_USERNAME_ATTEMPTS):
        yield jit_fallback_username(issuer, subject, attempt)


def provision_external_user(
    db: Session,
    issuer: str,
    subject: str,
    preferred_username: object = "",
    provider: str = IDENTITY_PROVIDER_OIDC,
) -> tuple[User, ExternalIdentity] | None:
    """Create a passwordless viewer for a new external identity in one transaction.

    User, preferences, saved views and the identity row are written inside a
    single savepoint, so a concurrent first sign-in of the same identity can
    never leave half of them behind.
    """
    for username in _jit_usernames(db, issuer, subject, preferred_username):
        identity = ExternalIdentity(provider=provider, issuer=issuer, subject=subject)
        try:
            with db.begin_nested():
                user = create_passwordless_user(db, username)
                identity.user_id = user.id
                db.add(identity)
                db.flush()
        except IntegrityError:
            existing = find_external_identity(db, issuer, subject, provider)
            if existing is None:
                # Only the username collided, so try the next deterministic one.
                continue
            existing_user = db.query(User).filter(User.id == existing.user_id).first()
            return (existing_user, existing) if existing_user is not None else None
        return user, identity
    return None


def active_oidc_admin_count(db: Session, issuer: str, exclude_user_id: int | None = None) -> int:
    """Count active administrators linked to one exact OIDC issuer."""
    query = (
        db.query(User)
        .join(ExternalIdentity, ExternalIdentity.user_id == User.id)
        .filter(
            User.role == "admin",
            User.is_active == True,  # noqa: E712
            ExternalIdentity.provider == IDENTITY_PROVIDER_OIDC,
            ExternalIdentity.issuer == issuer,
        )
    )
    if exclude_user_id is not None:
        query = query.filter(User.id != exclude_user_id)
    return query.count()
