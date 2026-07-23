import time
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import httpx
import httpx2
import pytest
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import auth as auth_api
from app.api import oidc_auth
from app.core.template_context import get_setting_value
from app.database.base import Base
from app.database.dependencies import get_db
from app.main import app
from app.models.users import ExternalIdentity, User, UserPreference, UserSession
from app.services import oidc
from app.services.auth import (
    AUTH_DISABLED_ENV,
    AUTH_METHOD_OIDC,
    AUTH_METHOD_PASSWORD,
    auth_enabled,
    create_user,
    jit_fallback_username,
    link_external_identity,
    provision_external_user,
)
from app.services.oidc import (
    OIDC_CLIENT_ID_SETTING,
    OIDC_CLIENT_SECRET_SETTING,
    OIDC_DISCOVERY_URL_SETTING,
    OIDC_ENABLED_SETTING,
    OIDC_ISSUER_SETTING,
    OIDC_JIT_ENABLED_SETTING,
    PASSWORD_LOGIN_ENABLED_SETTING,
    OidcConfigurationError,
    build_oauth_client,
    effective_password_login_enabled,
    load_config,
)
from app.web import auth as auth_web
from app.web.tables import save_setting

ISSUER = "https://idp.example.test"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"
OTHER_DISCOVERY_URL = "https://other-idp.example.test/.well-known/openid-configuration"
OTHER_ISSUER = "https://other-idp.example.test"
CLIENT_ID = "dashboard-client-1234"
CLIENT_SECRET = "provider-secret"
ADMIN_SUBJECT = "0000-subject-admin"
NEW_SUBJECT = "0000-subject-newcomer"


def _metadata():
    return {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "jwks_uri": f"{ISSUER}/jwks",
        "userinfo_endpoint": f"{ISSUER}/userinfo",
        "response_types_supported": ["code"],
        "id_token_signing_alg_values_supported": ["RS256"],
    }


class FakeProvider:
    """A deterministic in-process OpenID provider for the redirect handshake."""

    def __init__(self) -> None:
        self.key = RSAKey.generate_key(2048, parameters={"kid": "test-key-1", "use": "sig", "alg": "RS256"})
        self.published_keys = KeySet([self.key])
        self.nonce = ""
        self.claim_overrides: dict[str, object] = {}

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def claims(self) -> dict[str, object]:
        now = int(time.time())
        payload: dict[str, object] = {
            "iss": ISSUER,
            "sub": ADMIN_SUBJECT,
            "aud": CLIENT_ID,
            "exp": now + 300,
            "iat": now,
            "nonce": self.nonce,
            "preferred_username": "provider-person",
            "email": "provider-person@idp.example.test",
        }
        payload.update(self.claim_overrides)
        return payload

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "provider-access-token-value",
                    "token_type": "Bearer",
                    "expires_in": 300,
                    "id_token": jwt.encode({"alg": "RS256", "kid": self.key.kid}, self.claims(), self.key),
                },
            )
        if request.url.path == "/jwks":
            return httpx.Response(200, json=self.published_keys.as_dict(private=False))
        return httpx.Response(404, json={})


@pytest.fixture()
def provider(monkeypatch) -> FakeProvider:
    fake = FakeProvider()

    async def fake_metadata(config, **kwargs):
        return _metadata()

    monkeypatch.setattr(oidc_auth, "load_provider_metadata", fake_metadata)
    monkeypatch.setattr(
        oidc_auth,
        "build_oauth_client",
        lambda config, metadata: build_oauth_client(config, metadata, transport=fake.transport),
    )
    return fake


@pytest.fixture()
def methods_app(tmp_path: Path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'oidc-methods.db'}", connect_args={"check_same_thread": False})
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = session_factory()

    def get_test_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.delenv(AUTH_DISABLED_ENV, raising=False)
    monkeypatch.setattr(auth_web, "SessionLocal", session_factory)
    monkeypatch.setattr("app.main.SessionLocal", session_factory)
    monkeypatch.setattr("app.main.init_db", lambda: None)
    app.dependency_overrides[get_db] = get_test_db
    auth_api.reset_login_backoff()

    save_setting(db, "auth.enabled", "true")
    save_setting(db, "auth.hostname", "testserver")
    save_setting(db, OIDC_DISCOVERY_URL_SETTING, DISCOVERY_URL)
    save_setting(db, OIDC_CLIENT_ID_SETTING, CLIENT_ID)
    save_setting(db, OIDC_CLIENT_SECRET_SETTING, CLIENT_SECRET)
    save_setting(db, OIDC_ISSUER_SETTING, ISSUER)
    save_setting(db, OIDC_ENABLED_SETTING, "true")
    admin = create_user(db, "admin", "password123", "admin")
    create_user(db, "viewer", "password123", "viewer")
    db.flush()
    link_external_identity(db, admin.id, ISSUER, ADMIN_SUBJECT)
    db.commit()
    oidc.invalidate_provider_cache()

    client = TestClient(app, base_url="https://testserver")
    try:
        yield db, client
    finally:
        client.close()
        app.dependency_overrides.clear()
        auth_api.reset_login_backoff()
        oidc.invalidate_provider_cache()
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _password_sign_in(client: TestClient, username: str = "admin") -> httpx2.Response:
    return client.post(
        "/login",
        data={"username": username, "password": "password123"},
        follow_redirects=False,
    )


def _oidc_sign_in(client: TestClient, provider: FakeProvider) -> httpx2.Response:
    start = client.get("/auth/oidc/login", follow_redirects=False)
    params = dict(parse_qsl(urlsplit(start.headers["location"]).query))
    provider.nonce = params["nonce"]
    return client.get(f"/auth/oidc/callback?code=provider-code&state={params['state']}", follow_redirects=False)


def _oidc_sign_in_outcome(client: TestClient, provider: FakeProvider) -> str:
    """Run a sign-in that may already fail at the redirect and return where it ended."""
    start = client.get("/auth/oidc/login", follow_redirects=False)
    location = start.headers["location"]
    if not location.startswith("http"):
        return location
    params = dict(parse_qsl(urlsplit(location).query))
    provider.nonce = params["nonce"]
    callback = client.get(f"/auth/oidc/callback?code=provider-code&state={params['state']}", follow_redirects=False)
    return callback.headers["location"]


def _enable_jit(db) -> None:
    save_setting(db, OIDC_JIT_ENABLED_SETTING, "true")
    db.commit()


def _disable_password_login(client: TestClient) -> httpx2.Response:
    return client.post("/settings/auth/password-login/disable", follow_redirects=False)


def _sign_in_as_oidc_admin(db, client: TestClient, provider: FakeProvider) -> None:
    assert _oidc_sign_in(client, provider).status_code == 303
    assert db.query(UserSession).one().auth_method == AUTH_METHOD_OIDC


# --- just-in-time provisioning ---------------------------------------------


def test_an_unknown_identity_is_rejected_while_just_in_time_is_off(methods_app, provider):
    db, client = methods_app
    provider.claim_overrides = {"sub": NEW_SUBJECT}

    response = _oidc_sign_in(client, provider)

    assert response.headers["location"] == "/login?oidc_error=login_failed"
    assert db.query(User).count() == 2
    assert db.query(ExternalIdentity).count() == 1
    assert db.query(UserSession).count() == 0


def test_just_in_time_creates_exactly_one_active_viewer(methods_app, provider):
    db, client = methods_app
    _enable_jit(db)
    provider.claim_overrides = {"sub": NEW_SUBJECT}

    response = _oidc_sign_in(client, provider)

    assert response.headers["location"] == "/"
    created = db.query(User).filter(User.username == "provider-person").one()
    assert (created.role, created.is_active, created.password_hash) == ("viewer", True, None)
    assert db.query(UserPreference).filter(UserPreference.user_id == created.id).count() == 1
    identity = db.query(ExternalIdentity).filter(ExternalIdentity.user_id == created.id).one()
    assert (identity.issuer, identity.subject) == (ISSUER, NEW_SUBJECT)
    assert db.query(UserSession).one().auth_method == AUTH_METHOD_OIDC


@pytest.mark.parametrize(
    "claims",
    [
        {"groups": ["administrators", "admin"]},
        {"roles": ["admin"]},
        {"realm_access": {"roles": ["admin"]}},
        {"resource_access": {"opensecdash": {"roles": ["admin"]}}},
        {"role": "admin"},
    ],
)
def test_no_provider_claim_can_raise_the_local_role(methods_app, provider, claims):
    db, client = methods_app
    _enable_jit(db)
    provider.claim_overrides = {"sub": NEW_SUBJECT, **claims}

    _oidc_sign_in(client, provider)

    assert db.query(User).filter(User.username == "provider-person").one().role == "viewer"
    assert client.get("/settings", follow_redirects=False).status_code == 403


def test_a_taken_or_invalid_provider_name_falls_back_to_a_stable_hash(methods_app, provider):
    db, client = methods_app
    _enable_jit(db)
    provider.claim_overrides = {"sub": NEW_SUBJECT, "preferred_username": "Admin"}

    _oidc_sign_in(client, provider)

    fallback = jit_fallback_username(ISSUER, NEW_SUBJECT)
    created = db.query(User).filter(User.username == fallback).one()
    assert created.role == "viewer"
    # The existing local account keeps its own identity and password.
    assert db.query(ExternalIdentity).filter(ExternalIdentity.subject == NEW_SUBJECT).one().user_id == created.id
    assert db.query(User).filter(User.username == "admin").one().password_hash is not None
    assert NEW_SUBJECT not in fallback


def test_an_unusable_provider_name_falls_back_too(methods_app, provider):
    db, client = methods_app
    _enable_jit(db)
    provider.claim_overrides = {"sub": NEW_SUBJECT, "preferred_username": "Not A Username!"}

    _oidc_sign_in(client, provider)

    assert db.query(User).filter(User.username == jit_fallback_username(ISSUER, NEW_SUBJECT)).count() == 1


def test_a_repeated_first_sign_in_never_creates_a_second_user(methods_app, provider):
    db, client = methods_app
    _enable_jit(db)
    provider.claim_overrides = {"sub": NEW_SUBJECT}

    _oidc_sign_in(client, provider)
    second_client = TestClient(app, base_url="https://testserver")
    try:
        _oidc_sign_in(second_client, provider)
    finally:
        second_client.close()

    assert db.query(User).count() == 3
    assert db.query(ExternalIdentity).filter(ExternalIdentity.subject == NEW_SUBJECT).count() == 1


def test_a_lost_provisioning_race_leaves_no_half_written_user(methods_app, provider):
    db, _client = methods_app
    first = provision_external_user(db, ISSUER, NEW_SUBJECT, "provider-person")
    db.commit()
    assert first is not None
    users_before = db.query(User).count()
    preferences_before = db.query(UserPreference).count()

    second = provision_external_user(db, ISSUER, NEW_SUBJECT, "provider-person")
    db.commit()

    assert second is not None
    assert second[0].id == first[0].id
    assert db.query(User).count() == users_before
    assert db.query(UserPreference).count() == preferences_before
    assert db.query(ExternalIdentity).filter(ExternalIdentity.subject == NEW_SUBJECT).count() == 1


def test_a_passwordless_user_can_never_sign_in_locally(methods_app, provider):
    db, client = methods_app
    _enable_jit(db)
    provider.claim_overrides = {"sub": NEW_SUBJECT}
    _oidc_sign_in(client, provider)
    db.query(UserSession).delete()
    db.commit()

    fresh = TestClient(app, base_url="https://testserver")
    try:
        response = fresh.post(
            "/login",
            data={"username": "provider-person", "password": "password123"},
            follow_redirects=False,
        )
    finally:
        fresh.close()

    assert response.status_code == 401
    assert db.query(UserSession).count() == 0


# --- effective sign-in methods ---------------------------------------------


def test_a_fresh_installation_starts_with_every_switch_at_its_default(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        config = load_config(db)

        assert auth_enabled(db) is False
        assert (config.enabled, config.jit_enabled) == (False, False)
        assert (config.discovery_url, config.client_id, config.client_secret, config.issuer) == ("", "", "", "")
        assert effective_password_login_enabled(db) is True
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_password_sign_in_stays_the_default(methods_app, provider):
    db, client = methods_app

    assert effective_password_login_enabled(db) is True
    assert 'name="password"' in client.get("/login").text


def test_a_disabled_password_sign_in_checks_no_hash_and_counts_no_failure(methods_app, provider, monkeypatch):
    db, client = methods_app
    save_setting(db, PASSWORD_LOGIN_ENABLED_SETTING, "false")
    db.commit()
    monkeypatch.setattr(auth_api, "authenticate", lambda *args, **kwargs: pytest.fail("password was verified"))
    monkeypatch.setattr(auth_api, "_record_failed_login", lambda *args: pytest.fail("backoff was changed"))

    response = _password_sign_in(client)

    assert response.status_code == 403
    assert "Password sign-in is switched off" in response.text
    assert db.query(UserSession).count() == 0


def test_the_login_page_hides_the_password_form_while_it_is_off(methods_app, provider):
    db, client = methods_app
    save_setting(db, PASSWORD_LOGIN_ENABLED_SETTING, "false")
    db.commit()

    page = client.get("/login")

    assert 'name="password"' not in page.text
    assert "/auth/oidc/login" in page.text


def test_the_account_page_hides_the_password_change_while_it_is_off(methods_app, provider):
    db, client = methods_app
    _sign_in_as_oidc_admin(db, client, provider)
    save_setting(db, PASSWORD_LOGIN_ENABLED_SETTING, "false")
    db.commit()

    page = client.get("/account")
    change = client.post(
        "/auth/password",
        data={"current_password": "password123", "new_password": "password456", "new_password_confirm": "password456"},
        follow_redirects=False,
    )

    assert 'action="/auth/password"' not in page.text
    assert change.headers["location"] == "/account?auth_error=password_login_disabled"
    assert db.query(User).filter(User.username == "admin").one().password_hash is not None


def test_a_stored_off_switch_never_survives_an_unusable_provider(methods_app, provider):
    db, client = methods_app
    save_setting(db, PASSWORD_LOGIN_ENABLED_SETTING, "false")
    save_setting(db, OIDC_ENABLED_SETTING, "false")
    db.commit()

    assert effective_password_login_enabled(db) is True
    assert 'name="password"' in client.get("/login").text


# --- switching password sign-in off ----------------------------------------


def test_switching_off_needs_an_oidc_session(methods_app, provider):
    db, client = methods_app
    assert _password_sign_in(client).status_code == 303

    response = _disable_password_login(client)

    assert response.headers["location"] == "/settings?oidc_error=password_login_needs_oidc_session"
    assert effective_password_login_enabled(db) is True


def test_switching_off_needs_an_enabled_provider(methods_app, provider):
    db, client = methods_app
    _sign_in_as_oidc_admin(db, client, provider)
    save_setting(db, OIDC_ENABLED_SETTING, "false")
    db.commit()

    response = _disable_password_login(client)

    assert response.headers["location"] == "/settings?oidc_error=password_login_needs_oidc"
    assert effective_password_login_enabled(db) is True


def test_switching_off_needs_a_link_to_the_configured_issuer(methods_app, provider):
    db, client = methods_app
    _sign_in_as_oidc_admin(db, client, provider)
    db.query(ExternalIdentity).one().issuer = OTHER_ISSUER
    db.commit()

    response = _disable_password_login(client)

    assert response.headers["location"] == "/settings?oidc_error=password_login_needs_link"
    assert effective_password_login_enabled(db) is True


def test_switching_off_needs_an_admin(methods_app, provider):
    db, client = methods_app
    _sign_in_as_oidc_admin(db, client, provider)
    db.query(User).filter(User.username == "admin").one().role = "viewer"
    db.commit()

    response = _disable_password_login(client)

    assert response.status_code == 403
    assert effective_password_login_enabled(db) is True


def test_switching_off_succeeds_from_an_oidc_admin_session_and_revokes_password_sessions(methods_app, provider):
    db, client = methods_app
    password_client = TestClient(app, base_url="https://testserver")
    try:
        assert _password_sign_in(password_client, "viewer").status_code == 303
        _sign_in_as_oidc_admin_after_password(db, client, provider)

        response = _disable_password_login(client)

        assert response.headers["location"] == "/settings?oidc_notice=password_login_disabled"
        assert effective_password_login_enabled(db) is False
        assert [session.auth_method for session in db.query(UserSession).all()] == [AUTH_METHOD_OIDC]
        assert password_client.get("/", follow_redirects=False).headers["location"].startswith("/login")
    finally:
        password_client.close()


def _sign_in_as_oidc_admin_after_password(db, client: TestClient, provider: FakeProvider) -> None:
    assert _oidc_sign_in(client, provider).status_code == 303
    assert db.query(UserSession).filter(UserSession.auth_method == AUTH_METHOD_OIDC).count() == 1


def test_switching_back_on_revokes_nothing(methods_app, provider):
    db, client = methods_app
    _sign_in_as_oidc_admin(db, client, provider)
    assert _disable_password_login(client).status_code == 303

    response = client.post("/settings/auth/password-login/enable", follow_redirects=False)

    assert response.headers["location"] == "/settings?oidc_notice=password_login_enabled"
    assert effective_password_login_enabled(db) is True
    assert db.query(UserSession).one().auth_method == AUTH_METHOD_OIDC


# --- changing or disabling the provider ------------------------------------


def test_disabling_the_provider_restores_password_sign_in_and_revokes_its_sessions(methods_app, provider):
    db, client = methods_app
    _sign_in_as_oidc_admin(db, client, provider)
    assert _disable_password_login(client).status_code == 303

    response = client.post("/settings/auth/oidc/disable", follow_redirects=False)

    assert response.headers["location"] == "/settings?oidc_notice=disabled"
    assert get_setting_value(db, PASSWORD_LOGIN_ENABLED_SETTING, "") == "true"
    assert load_config(db).enabled is False
    assert db.query(UserSession).count() == 0


def test_the_provider_cannot_be_changed_while_password_sign_in_is_off(methods_app, provider):
    db, client = methods_app
    _sign_in_as_oidc_admin(db, client, provider)
    assert _disable_password_login(client).status_code == 303

    response = client.post(
        "/settings/auth/oidc",
        data={"discovery_url": OTHER_DISCOVERY_URL, "client_id": CLIENT_ID, "client_secret": ""},
        follow_redirects=False,
    )

    assert response.headers["location"] == "/settings?oidc_error=password_login_locked"
    assert load_config(db).discovery_url == DISCOVERY_URL


def test_changing_the_provider_revokes_provider_sessions(methods_app, provider, monkeypatch):
    db, client = methods_app
    _sign_in_as_oidc_admin(db, client, provider)

    async def fake_check(discovery_url, **kwargs):
        return OTHER_ISSUER, _metadata()

    monkeypatch.setattr("app.api.oidc_settings.check_provider", fake_check)
    response = client.post(
        "/settings/auth/oidc",
        data={"discovery_url": OTHER_DISCOVERY_URL, "client_id": CLIENT_ID, "client_secret": ""},
        follow_redirects=False,
    )

    assert response.headers["location"] == "/settings?oidc_notice=configuration_saved"
    assert load_config(db).issuer == OTHER_ISSUER
    assert db.query(UserSession).count() == 0
    # Existing links stay with the issuer they were created for.
    assert db.query(ExternalIdentity).one().issuer == ISSUER


def test_switching_just_in_time_changes_no_user_and_no_session(methods_app, provider):
    db, client = methods_app
    _sign_in_as_oidc_admin(db, client, provider)
    users_before = {user.id: (user.role, user.is_active) for user in db.query(User).all()}

    response = client.post("/settings/auth/oidc/jit", data={"jit_enabled": "true"}, follow_redirects=False)

    assert response.headers["location"] == "/settings?oidc_notice=jit_saved"
    assert load_config(db).jit_enabled is True
    assert {user.id: (user.role, user.is_active) for user in db.query(User).all()} == users_before
    assert db.query(UserSession).count() == 1


# --- the last reachable admin ----------------------------------------------


@pytest.fixture()
def only_oidc(methods_app, provider):
    """An instance where the signed-in admin is the only way back in."""
    db, client = methods_app
    _sign_in_as_oidc_admin(db, client, provider)
    assert _disable_password_login(client).status_code == 303
    return db, client, db.query(User).filter(User.username == "admin").one()


def test_the_last_reachable_admin_survives_every_mutation(only_oidc):
    db, client, admin = only_oidc

    demote = client.post(f"/settings/users/{admin.id}/role", data={"role": "viewer"}, follow_redirects=False)
    deactivate = client.post(f"/settings/users/{admin.id}/toggle", follow_redirects=False)
    delete = client.post(f"/settings/users/{admin.id}/delete", follow_redirects=False)
    revoke = client.post(f"/settings/users/{admin.id}/oidc/unlink", follow_redirects=False)

    for response in (demote, deactivate):
        assert response.headers["location"] == "/settings?auth_error=last_admin"
    assert delete.headers["location"] == "/settings?auth_error=self_delete"
    assert revoke.headers["location"] == "/settings?auth_error=self_unlink"
    db.refresh(admin)
    assert (admin.role, admin.is_active) == ("admin", True)
    assert db.query(ExternalIdentity).count() == 1


def test_the_last_reachable_admin_is_protected_against_another_admin(only_oidc):
    db, client, admin = only_oidc
    reachable = create_user(db, "reachable-admin", "password123", "admin")
    db.flush()
    link_external_identity(db, reachable.id, ISSUER, "0000-subject-reachable")
    # The signed-in admin keeps their session but loses the link that made them
    # reachable, so only the other admin can still sign in.
    db.query(ExternalIdentity).filter(ExternalIdentity.user_id == admin.id).one().issuer = OTHER_ISSUER
    db.commit()

    demote = client.post(f"/settings/users/{reachable.id}/role", data={"role": "operator"}, follow_redirects=False)
    deactivate = client.post(f"/settings/users/{reachable.id}/toggle", follow_redirects=False)
    delete = client.post(f"/settings/users/{reachable.id}/delete", follow_redirects=False)
    revoke = client.post(f"/settings/users/{reachable.id}/oidc/unlink", follow_redirects=False)

    for response in (demote, deactivate, delete, revoke):
        assert response.headers["location"] == "/settings?auth_error=last_oidc_admin"
    db.refresh(reachable)
    assert (reachable.role, reachable.is_active) == ("admin", True)
    assert db.query(ExternalIdentity).filter(ExternalIdentity.user_id == reachable.id).count() == 1


def test_an_admin_without_a_usable_link_cannot_replace_the_reachable_one(only_oidc, provider):
    db, client, admin = only_oidc
    second = create_user(db, "second-admin", "password123", "admin")
    db.flush()
    link_external_identity(db, second.id, OTHER_ISSUER, "0000-subject-other")
    db.commit()

    demote = client.post(f"/settings/users/{admin.id}/role", data={"role": "viewer"}, follow_redirects=False)

    assert demote.headers["location"] == "/settings?auth_error=last_oidc_admin"
    db.refresh(admin)
    assert admin.role == "admin"


def test_a_second_reachable_admin_makes_the_first_one_changeable(only_oidc, provider):
    db, client, admin = only_oidc
    second = create_user(db, "second-admin", "password123", "admin")
    db.flush()
    link_external_identity(db, second.id, ISSUER, "0000-subject-second")
    db.commit()

    response = client.post(f"/settings/users/{admin.id}/role", data={"role": "viewer"}, follow_redirects=False)

    assert response.headers["location"] == "/settings"
    db.refresh(admin)
    assert admin.role == "viewer"


def test_an_admin_revoke_ends_the_access_of_another_user(methods_app, provider):
    db, client = methods_app
    _enable_jit(db)
    provider.claim_overrides = {"sub": NEW_SUBJECT}
    jit_client = TestClient(app, base_url="https://testserver")
    try:
        _oidc_sign_in(jit_client, provider)
        created = db.query(User).filter(User.username == "provider-person").one()
        assert _password_sign_in(client).status_code == 303

        response = client.post(f"/settings/users/{created.id}/oidc/unlink", follow_redirects=False)

        assert response.headers["location"] == "/settings"
        assert db.query(ExternalIdentity).filter(ExternalIdentity.user_id == created.id).count() == 0
        assert db.query(UserSession).filter(UserSession.user_id == created.id).count() == 0
        assert jit_client.get("/", follow_redirects=False).headers["location"].startswith("/login")
    finally:
        jit_client.close()


def test_an_admin_revoke_needs_an_existing_link(methods_app, provider):
    db, client = methods_app
    assert _password_sign_in(client).status_code == 303
    viewer = db.query(User).filter(User.username == "viewer").one()

    response = client.post(f"/settings/users/{viewer.id}/oidc/unlink", follow_redirects=False)

    assert response.headers["location"] == "/settings?auth_error=not_linked"


# --- removing your own link ------------------------------------------------


def test_a_user_can_remove_their_own_link_and_is_signed_out(methods_app, provider):
    db, client = methods_app
    _sign_in_as_oidc_admin(db, client, provider)

    response = client.post("/account/oidc/unlink", follow_redirects=False)

    assert response.headers["location"] == "/login"
    assert db.query(ExternalIdentity).count() == 0
    assert db.query(UserSession).count() == 0


def test_removing_your_own_link_needs_a_local_password(methods_app, provider):
    db, client = methods_app
    _sign_in_as_oidc_admin(db, client, provider)
    db.query(User).filter(User.username == "admin").one().password_hash = None
    db.commit()

    response = client.post("/account/oidc/unlink", follow_redirects=False)

    assert response.headers["location"] == "/account?oidc_error=unlink_needs_password"
    assert db.query(ExternalIdentity).count() == 1


def test_removing_your_own_link_needs_password_sign_in(only_oidc):
    db, client, _admin = only_oidc

    response = client.post("/account/oidc/unlink", follow_redirects=False)

    assert response.headers["location"] == "/account?oidc_error=unlink_needs_password_login"
    assert db.query(ExternalIdentity).count() == 1


# --- emergency access ------------------------------------------------------


@pytest.fixture()
def break_glass(only_oidc, monkeypatch):
    """Emergency access on an instance whose provider is the only way in."""
    db, client, admin = only_oidc
    monkeypatch.setenv(AUTH_DISABLED_ENV, "true")
    return db, client, admin


def test_emergency_access_can_switch_password_sign_in_back_on(break_glass):
    db, client, _admin = break_glass

    response = client.post("/settings/auth/password-login/enable", follow_redirects=False)

    assert response.headers["location"] == "/settings?oidc_notice=password_login_enabled"
    assert get_setting_value(db, PASSWORD_LOGIN_ENABLED_SETTING, "") == "true"
    assert db.query(UserSession).count() == 0


def test_emergency_access_can_disable_the_provider_without_touching_its_configuration(break_glass):
    db, client, _admin = break_glass

    response = client.post("/settings/auth/oidc/disable", follow_redirects=False)

    assert response.headers["location"] == "/settings?oidc_notice=disabled"
    config = load_config(db)
    assert config.enabled is False
    assert (config.discovery_url, config.client_id, config.client_secret) == (DISCOVERY_URL, CLIENT_ID, CLIENT_SECRET)
    assert get_setting_value(db, PASSWORD_LOGIN_ENABLED_SETTING, "") == "true"
    assert db.query(ExternalIdentity).count() == 1


def test_emergency_access_can_repair_the_provider_configuration(break_glass, monkeypatch):
    db, client, _admin = break_glass

    async def fake_check(discovery_url, **kwargs):
        return OTHER_ISSUER, _metadata()

    monkeypatch.setattr("app.api.oidc_settings.check_provider", fake_check)
    response = client.post(
        "/settings/auth/oidc",
        data={"discovery_url": OTHER_DISCOVERY_URL, "client_id": "repaired-client", "client_secret": ""},
        follow_redirects=False,
    )

    assert response.headers["location"] == "/settings?oidc_notice=configuration_saved"
    config = load_config(db)
    assert (config.discovery_url, config.client_id, config.issuer) == (OTHER_DISCOVERY_URL, "repaired-client", OTHER_ISSUER)
    assert config.client_secret == CLIENT_SECRET
    assert db.query(UserSession).count() == 0


def test_emergency_access_can_reset_a_local_password(break_glass):
    db, client, admin = break_glass
    before = admin.password_hash

    response = client.post(
        "/settings/users/password",
        data={"user_id": admin.id, "password": "recovered-password"},
        follow_redirects=False,
    )
    db.refresh(admin)

    assert response.headers["location"] == "/settings"
    assert admin.password_hash != before
    assert db.query(UserSession).count() == 0


def test_the_recovery_page_offers_the_repair_actions_without_the_secret(break_glass):
    _db, client = break_glass[0], break_glass[1]

    page = client.get("/settings")

    assert 'action="/settings/auth/password-login/enable"' in page.text
    assert 'action="/settings/auth/oidc/disable"' in page.text
    assert 'action="/settings/users/password"' in page.text
    assert "OSD_AUTH_DISABLED" in page.text
    assert CLIENT_SECRET not in page.text


def test_recovery_routes_stay_closed_without_emergency_access(methods_app, provider):
    _db, client = methods_app

    enable = client.post("/settings/auth/password-login/enable", follow_redirects=False)
    hostname = client.post("/settings/auth/hostname", data={"hostname": "other.example"}, follow_redirects=False)

    assert enable.headers["location"].startswith("/login")
    assert hostname.headers["location"].startswith("/login")


def test_a_disabled_setting_alone_is_not_the_recovery_mode(methods_app, provider):
    db, client = methods_app
    assert _password_sign_in(client).status_code == 303
    save_setting(db, "auth.enabled", "false")
    db.commit()

    response = client.post("/settings/auth/hostname", data={"hostname": "other.example"}, follow_redirects=False)

    assert response.headers["location"] == "/settings?auth_error=recovery_only"
    assert get_setting_value(db, "auth.hostname", "") == "testserver"


def test_removing_the_environment_switch_restores_the_stored_state(break_glass, monkeypatch):
    db, client, _admin = break_glass
    stored = load_config(db)

    monkeypatch.delenv(AUTH_DISABLED_ENV)
    visitor = TestClient(app, base_url="https://testserver")
    try:
        response = visitor.get("/", follow_redirects=False)
    finally:
        visitor.close()

    assert response.headers["location"].startswith("/login")
    assert load_config(db) == stored
    assert get_setting_value(db, PASSWORD_LOGIN_ENABLED_SETTING, "") == "false"
    assert effective_password_login_enabled(db) is False


def _break_provider(provider: FakeProvider, monkeypatch, failure: str) -> None:
    """Make the configured provider fail the way one real outage class would."""
    if failure in ("dns", "timeout", "tls", "discovery"):
        # Which network exception maps to which sanitized code is covered in
        # test_oidc_configuration.py; here only the effect on sign-in matters.
        code = "invalid_response" if failure == "discovery" else "unreachable"

        async def failing_metadata(config, **kwargs):
            raise OidcConfigurationError(code)

        monkeypatch.setattr(oidc_auth, "load_provider_metadata", failing_metadata)
        return

    broken_path = "/jwks" if failure == "jwks" else "/token"

    def failing_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == broken_path:
            return httpx.Response(500, json={})
        return FakeProvider._handle(provider, request)

    monkeypatch.setattr(provider, "_handle", failing_handler)


@pytest.mark.parametrize("failure", ["dns", "timeout", "tls", "discovery", "jwks", "token"])
def test_emergency_access_recovers_from_every_provider_failure(only_oidc, provider, monkeypatch, failure):
    db, client, _admin = only_oidc
    _break_provider(provider, monkeypatch, failure)
    assert client.post("/auth/logout", follow_redirects=False).status_code == 303

    assert _oidc_sign_in_outcome(client, provider).startswith("/login?oidc_error=")
    assert _password_sign_in(client).status_code == 403

    monkeypatch.setenv(AUTH_DISABLED_ENV, "true")
    assert client.post("/settings/auth/password-login/enable", follow_redirects=False).status_code == 303
    monkeypatch.delenv(AUTH_DISABLED_ENV)

    assert _password_sign_in(client).status_code == 303
    assert db.query(UserSession).one().auth_method == AUTH_METHOD_PASSWORD
    assert load_config(db).enabled is True


def test_the_last_method_cannot_be_taken_away(methods_app, provider):
    db, client = methods_app
    _sign_in_as_oidc_admin(db, client, provider)
    assert _disable_password_login(client).status_code == 303

    blocked = client.post("/settings/auth/oidc/secret/delete", follow_redirects=False)

    assert blocked.headers["location"] == "/settings?oidc_error=password_login_locked"
    assert load_config(db).client_secret == CLIENT_SECRET
    assert effective_password_login_enabled(db) is False


def test_deleting_the_secret_leaves_password_sign_in_as_the_only_method(methods_app, provider):
    db, client = methods_app
    _sign_in_as_oidc_admin(db, client, provider)

    response = client.post("/settings/auth/oidc/secret/delete", follow_redirects=False)

    assert response.headers["location"] == "/settings?oidc_notice=secret_deleted"
    assert get_setting_value(db, PASSWORD_LOGIN_ENABLED_SETTING, "true") == "true"
    assert effective_password_login_enabled(db) is True
    assert load_config(db).enabled is False


# --- user interface --------------------------------------------------------


def test_the_settings_page_explains_the_login_methods_in_both_languages(methods_app, provider):
    db, client = methods_app
    assert _password_sign_in(client).status_code == 303

    english = client.get("/settings")
    save_setting(db, "language", "de")
    db.query(UserPreference).filter(
        UserPreference.user_id == db.query(User).filter(User.username == "admin").one().id
    ).one().language = "de"
    db.commit()
    german = client.get("/settings")

    assert "Password sign-in" in english.text
    assert "new users become viewers" in english.text
    assert "Linked accounts" in english.text
    assert "OSD_AUTH_DISABLED" in english.text
    assert "Anmeldung mit Passwort" in german.text
    assert "Verknüpfte Konten" in german.text
    assert "Rollen bleiben immer lokal" in german.text


def test_the_settings_page_shows_link_state_without_provider_details(methods_app, provider):
    _db, client = methods_app
    assert _password_sign_in(client).status_code == 303

    page = client.get("/settings")

    # The admin's own discovery URL is shown, the provider's answer about a
    # person never is.
    assert "Linked" in page.text
    assert ADMIN_SUBJECT not in page.text
    assert "provider-person" not in page.text
