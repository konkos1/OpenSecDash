from datetime import timedelta
import hashlib

import pytest

from app.core.time import utc_now
from app.models.saved_views import SavedView
from app.models.settings import Setting
from app.models.users import ExternalIdentity, UserPreference, UserSession
from app.services import auth as auth_service
from app.services.auth import (
    AUTH_DISABLED_ENV,
    AUTH_METHOD_OIDC,
    AUTH_METHOD_PASSWORD,
    active_oidc_admin_count,
    authenticate,
    auth_enabled,
    create_passwordless_user,
    create_session,
    create_user,
    delete_session,
    delete_sessions_by_auth_method,
    delete_user_external_identities,
    delete_user_sessions,
    find_external_identity,
    find_user_external_identity,
    hash_password,
    link_external_identity,
    normalize_auth_hostname,
    normalize_username,
    password_needs_rehash,
    resolve_session,
    unlink_external_identity,
    validate_new_user,
    verify_password,
)

ISSUER = "https://idp.example/realms/homelab"
OTHER_ISSUER = "https://other-idp.example"


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
    rehashed = user.password_hash
    assert rehashed is not None
    assert rehashed.startswith("scrypt$16384$8$5$")
    assert verify_password("password123", rehashed) is True


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
    token = create_session(db_session, user, AUTH_METHOD_PASSWORD)
    db_session.flush()

    session = db_session.query(UserSession).one()
    assert session.token_hash != token
    assert resolve_session(db_session, token) == user

    delete_session(db_session, token)
    assert resolve_session(db_session, token) is None

    first_token = create_session(db_session, user, AUTH_METHOD_PASSWORD)
    second_token = create_session(db_session, user, AUTH_METHOD_PASSWORD)
    delete_user_sessions(db_session, user.id)
    assert resolve_session(db_session, first_token) is None
    assert resolve_session(db_session, second_token) is None

    expired_token = create_session(db_session, user, AUTH_METHOD_PASSWORD)
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
    token = create_session(db_session, user, AUTH_METHOD_PASSWORD)
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


def test_passwordless_users_are_viewers_that_cannot_sign_in_with_a_password(db_session):
    user = create_passwordless_user(db_session, " Viewer ")
    db_session.flush()

    assert (user.username, user.role, user.password_hash) == ("viewer", "viewer", None)
    assert db_session.query(UserPreference).filter(UserPreference.user_id == user.id).count() == 1
    assert authenticate(db_session, "viewer", "password123") is None
    assert authenticate(db_session, "viewer", "") is None
    assert verify_password("password123", None) is False
    assert password_needs_rehash(None) is False


def test_passwordless_and_unknown_accounts_share_the_dummy_hash_path(db_session, monkeypatch):
    create_passwordless_user(db_session, "viewer")
    db_session.flush()
    verified: list[str | None] = []
    original_verify_password = auth_service.verify_password

    def recording_verify_password(password: str, stored: str | None) -> bool:
        verified.append(stored)
        return original_verify_password(password, stored)

    monkeypatch.setattr(auth_service, "verify_password", recording_verify_password)

    assert authenticate(db_session, "viewer", "password123") is None
    assert authenticate(db_session, "missing", "password123") is None
    assert len(verified) == 2
    assert verified[0] == verified[1]


def test_sessions_record_their_authentication_method_and_are_revocable_per_method(db_session):
    user = create_user(db_session, "admin", "password123", "admin")
    password_token = create_session(db_session, user, AUTH_METHOD_PASSWORD)
    oidc_token = create_session(db_session, user, AUTH_METHOD_OIDC)
    db_session.flush()

    assert resolve_session(db_session, password_token) == user
    assert resolve_session(db_session, oidc_token) == user
    assert sorted(session.auth_method for session in db_session.query(UserSession).all()) == [
        AUTH_METHOD_OIDC,
        AUTH_METHOD_PASSWORD,
    ]

    delete_sessions_by_auth_method(db_session, AUTH_METHOD_PASSWORD)
    assert resolve_session(db_session, password_token) is None
    assert resolve_session(db_session, oidc_token) == user

    with pytest.raises(ValueError):
        create_session(db_session, user, "magic-link")
    with pytest.raises(ValueError):
        delete_sessions_by_auth_method(db_session, "magic-link")


def test_external_identities_are_unique_per_subject_and_per_user(db_session):
    first = create_user(db_session, "first", "password123", "admin")
    second = create_user(db_session, "second", "password123", "viewer")
    db_session.flush()

    identity = link_external_identity(db_session, first.id, ISSUER, "subject-1")
    assert isinstance(identity, ExternalIdentity)
    assert (identity.provider, identity.issuer, identity.subject) == ("oidc", ISSUER, "subject-1")
    assert find_external_identity(db_session, ISSUER, "subject-1") == identity
    assert find_user_external_identity(db_session, first.id) == identity

    assert link_external_identity(db_session, second.id, ISSUER, "subject-1") == "identity_taken"
    assert link_external_identity(db_session, first.id, ISSUER, "subject-2") == "identity_already_linked"
    assert find_external_identity(db_session, ISSUER, "subject-2") is None
    assert find_user_external_identity(db_session, second.id) is None

    assert unlink_external_identity(db_session, first.id) is True
    assert unlink_external_identity(db_session, first.id) is False
    assert find_external_identity(db_session, ISSUER, "subject-1") is None


def test_linking_rejects_a_concurrently_created_duplicate_identity(db_session, monkeypatch):
    first = create_user(db_session, "first", "password123", "admin")
    second = create_user(db_session, "second", "password123", "viewer")
    db_session.flush()
    link_external_identity(db_session, first.id, ISSUER, "subject-1")
    db_session.flush()
    monkeypatch.setattr(auth_service, "find_external_identity", lambda *args, **kwargs: None)

    assert link_external_identity(db_session, second.id, ISSUER, "subject-1") == "identity_taken"
    assert find_user_external_identity(db_session, second.id) is None


def test_active_oidc_admin_count_matches_role_state_and_issuer(db_session):
    admin = create_user(db_session, "admin", "password123", "admin")
    inactive_admin = create_user(db_session, "inactive", "password123", "admin")
    operator = create_user(db_session, "operator", "password123", "operator")
    other_issuer_admin = create_user(db_session, "other", "password123", "admin")
    unlinked_admin = create_user(db_session, "unlinked", "password123", "admin")
    db_session.flush()
    inactive_admin.is_active = False
    for user, subject in ((admin, "s1"), (inactive_admin, "s2"), (operator, "s3")):
        link_external_identity(db_session, user.id, ISSUER, subject)
    link_external_identity(db_session, other_issuer_admin.id, OTHER_ISSUER, "s4")
    db_session.flush()

    assert active_oidc_admin_count(db_session, ISSUER) == 1
    assert active_oidc_admin_count(db_session, ISSUER, exclude_user_id=admin.id) == 0
    assert active_oidc_admin_count(db_session, OTHER_ISSUER) == 1
    assert unlinked_admin.role == "admin"


def test_deleting_user_identities_removes_only_that_users_rows(db_session):
    first = create_user(db_session, "first", "password123", "admin")
    second = create_user(db_session, "second", "password123", "viewer")
    db_session.flush()
    link_external_identity(db_session, first.id, ISSUER, "subject-1")
    link_external_identity(db_session, second.id, ISSUER, "subject-2")
    db_session.flush()

    delete_user_external_identities(db_session, first.id)

    assert find_user_external_identity(db_session, first.id) is None
    assert find_user_external_identity(db_session, second.id) is not None
