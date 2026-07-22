from datetime import timedelta
import hashlib

from app.core.time import utc_now
from app.models.saved_views import SavedView
from app.models.settings import Setting
from app.models.users import UserSession
from app.services.auth import (
    AUTH_DISABLED_ENV,
    authenticate,
    auth_enabled,
    create_session,
    create_user,
    delete_session,
    delete_user_sessions,
    hash_password,
    normalize_auth_hostname,
    normalize_username,
    password_needs_rehash,
    resolve_session,
    validate_new_user,
    verify_password,
)


def test_auth_hostname_normalization_accepts_dns_names_and_rejects_origins_and_ips():
    assert normalize_auth_hostname("OSD.Example.Internal") == "osd.example.internal"
    assert normalize_auth_hostname("münchen.example") == "xn--mnchen-3ya.example"
    for value in ("", "https://osd.example", "osd.example:443", "osd.example/", "osd.example.", "192.168.1.10", "-osd.example"):
        assert normalize_auth_hostname(value) is None


def test_password_hashes_are_salted_and_reject_invalid_values():
    first_hash = hash_password("password123")
    second_hash = hash_password("password123")

    assert first_hash.startswith("scrypt$16384$8$5$")
    assert first_hash != second_hash
    assert verify_password("password123", first_hash) is True
    assert verify_password("wrong-password", first_hash) is False
    assert verify_password("password123", "") is False
    assert verify_password("password123", "bcrypt$anything") is False
    assert verify_password("password123", first_hash[:-1]) is False
    assert password_needs_rehash(first_hash) is False


def test_legacy_password_hash_is_rehashed_only_after_successful_authentication(db_session):
    salt = bytes.fromhex("00112233445566778899aabbccddeeff")
    legacy_digest = hashlib.scrypt(b"password123", salt=salt, n=16384, r=8, p=1, dklen=32)
    legacy_hash = f"scrypt$16384$8$1${salt.hex()}${legacy_digest.hex()}"
    user = create_user(db_session, "admin", "password123", "admin")
    user.password_hash = legacy_hash
    db_session.commit()

    assert authenticate(db_session, "admin", "wrong-password") is None
    db_session.refresh(user)
    assert user.password_hash == legacy_hash

    assert authenticate(db_session, "admin", "password123") == user
    db_session.refresh(user)
    assert user.password_hash.startswith("scrypt$16384$8$5$")
    assert verify_password("password123", user.password_hash) is True


def test_extreme_scrypt_parameters_are_rejected_before_hashing(monkeypatch):
    called = False

    def unexpected_scrypt(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("scrypt must not run")

    monkeypatch.setattr(hashlib, "scrypt", unexpected_scrypt)

    extreme = "scrypt$1048576$8$1$00112233445566778899aabbccddeeff$" + "00" * 32
    assert verify_password("password123", extreme) is False
    assert called is False


def test_new_user_validation_normalizes_names_and_rejects_invalid_input(db_session):
    user = create_user(db_session, " Admin ", "password123", "admin")

    assert user.username == "admin"
    assert normalize_username(" Admin ") == "admin"
    assert validate_new_user(db_session, "ADMIN", "password123") == "username_taken"
    assert validate_new_user(db_session, "invalid name", "password123") == "invalid_username"
    assert validate_new_user(db_session, "viewer", "shortpw") == "password_too_short"


def test_new_user_receives_independent_copies_of_legacy_saved_views(db_session):
    legacy_view = SavedView(
        name="Legacy",
        scope="events",
        filter_json={"country": "DE"},
        query_json={"range": "24h"},
    )
    db_session.add(legacy_view)
    db_session.flush()

    first_user = create_user(db_session, "first", "password123", "viewer")
    second_user = create_user(db_session, "second", "password123", "viewer")
    db_session.flush()

    first_view = db_session.query(SavedView).filter(SavedView.user_id == first_user.id).one()
    second_view = db_session.query(SavedView).filter(SavedView.user_id == second_user.id).one()
    assert (first_view.name, first_view.filter_json, first_view.query_json) == (
        "Legacy",
        {"country": "DE"},
        {"range": "24h"},
    )
    assert (second_view.name, second_view.filter_json, second_view.query_json) == (
        "Legacy",
        {"country": "DE"},
        {"range": "24h"},
    )
    assert first_view.id != second_view.id
    assert legacy_view.user_id is None


def test_sessions_are_hashed_revocable_and_expire(db_session):
    user = create_user(db_session, "admin", "password123", "admin")
    token = create_session(db_session, user)
    db_session.flush()

    session = db_session.query(UserSession).one()
    assert session.token_hash != token
    assert resolve_session(db_session, token) == user

    delete_session(db_session, token)
    assert resolve_session(db_session, token) is None

    first_token = create_session(db_session, user)
    second_token = create_session(db_session, user)
    delete_user_sessions(db_session, user.id)
    assert resolve_session(db_session, first_token) is None
    assert resolve_session(db_session, second_token) is None

    expired_token = create_session(db_session, user)
    db_session.flush()
    expired_session = (
        db_session.query(UserSession)
        .filter(UserSession.token_hash == hashlib.sha256(expired_token.encode()).hexdigest())
        .one()
    )
    expired_session.expires_at = (utc_now() - timedelta(seconds=1)).replace(tzinfo=None)
    db_session.flush()
    assert resolve_session(db_session, expired_token) is None
    db_session.flush()
    assert (
        db_session.query(UserSession)
        .filter(UserSession.token_hash == hashlib.sha256(expired_token.encode()).hexdigest())
        .first()
        is None
    )


def test_inactive_users_cannot_resolve_sessions(db_session):
    user = create_user(db_session, "admin", "password123", "admin")
    token = create_session(db_session, user)
    user.is_active = False

    assert resolve_session(db_session, token) is None


def test_authentication_rejects_unknown_inactive_and_invalid_credentials(db_session):
    user = create_user(db_session, "admin", "password123", "admin")

    assert authenticate(db_session, "ADMIN", "password123") == user
    assert authenticate(db_session, "admin", "wrong-password") is None
    assert authenticate(db_session, "missing", "password123") is None

    user.is_active = False
    assert authenticate(db_session, "admin", "password123") is None


def test_auth_enabled_respects_setting_and_break_glass_environment(db_session, monkeypatch):
    assert auth_enabled(db_session) is False

    db_session.add(Setting(key="auth.enabled", value="true"))
    db_session.flush()
    assert auth_enabled(db_session) is True

    monkeypatch.setenv(AUTH_DISABLED_ENV, "true")
    assert auth_enabled(db_session) is False
