import base64
import io
import json
import logging
import time
import zipfile
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlsplit

import httpx
import httpx2
import pytest
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import KeySet, OctKey, RSAKey
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.websockets import WebSocketDisconnect

from app.api import auth as auth_api
from app.api import oidc_auth
from app.database.base import Base
from app.database.dependencies import get_db
from app.main import app
from app.models.settings import Setting
from app.models.users import ExternalIdentity, User, UserPreference, UserSession
from app.services import oidc
from app.services.auth import (
    AUTH_METHOD_OIDC,
    AUTH_METHOD_PASSWORD,
    create_user,
    link_external_identity,
)
from app.services.oidc import (
    OIDC_CLIENT_ID_SETTING,
    OIDC_CLIENT_SECRET_SETTING,
    OIDC_DISCOVERY_URL_SETTING,
    OIDC_ENABLED_SETTING,
    OIDC_ISSUER_SETTING,
    build_oauth_client,
    safe_id_token_algorithms,
)
from app.web import auth as auth_web
from app.web.oidc_state import OIDC_STATE_COOKIE
from app.services.settings import save_setting

ISSUER = "https://idp.example.test"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"
CLIENT_ID = "dashboard-client-1234"
CLIENT_SECRET = "provider-secret"
SUBJECT = "0000-subject-aaaa"
OTHER_SUBJECT = "0000-subject-bbbb"
KEY_ID = "test-key-1"
ACCESS_TOKEN = "provider-access-token-value"

#: Marker for "leave this claim out entirely".
DROP = object()


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


def _unsigned_segment(document: dict[str, object]) -> str:
    return base64.urlsafe_b64encode(json.dumps(document).encode()).rstrip(b"=").decode()


class FakeProvider:
    """A deterministic in-process OpenID provider for the redirect handshake."""

    def __init__(self) -> None:
        self.key = RSAKey.generate_key(2048, parameters={"kid": KEY_ID, "use": "sig", "alg": "RS256"})
        self.signing_key = self.key
        self.published_keys = KeySet([self.key])
        self.nonce = ""
        self.claim_overrides: dict[str, object] = {}
        self.algorithm = "RS256"
        self.token_requests: list[dict[str, str]] = []
        self.id_token = ""

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def claims(self) -> dict[str, object]:
        now = int(time.time())
        payload: dict[str, object] = {
            "iss": ISSUER,
            "sub": SUBJECT,
            "aud": CLIENT_ID,
            "exp": now + 300,
            "iat": now,
            "nonce": self.nonce,
            "preferred_username": "provider-person",
            "email": "provider-person@idp.example.test",
        }
        payload.update(self.claim_overrides)
        return {key: value for key, value in payload.items() if value is not DROP}

    def _build_id_token(self) -> str:
        claims = self.claims()
        if self.algorithm == "none":
            return f"{_unsigned_segment({'alg': 'none', 'typ': 'JWT'})}.{_unsigned_segment(claims)}."
        if self.algorithm == "HS256":
            return jwt.encode({"alg": "HS256"}, claims, OctKey.import_key(CLIENT_SECRET))
        return jwt.encode({"alg": self.algorithm, "kid": self.signing_key.kid}, claims, self.signing_key)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            self.token_requests.append(dict(parse_qsl(request.content.decode())))
            self.id_token = self._build_id_token()
            return httpx.Response(
                200,
                json={
                    "access_token": ACCESS_TOKEN,
                    "token_type": "Bearer",
                    "expires_in": 300,
                    "id_token": self.id_token,
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
def oidc_app(tmp_path: Path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'oidc-login.db'}", connect_args={"check_same_thread": False})
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = session_factory()

    def get_test_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

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
    create_user(db, "admin", "password123", "admin")
    linked = create_user(db, "linked", "password123", "viewer")
    db.flush()
    link_external_identity(db, linked.id, ISSUER, SUBJECT)
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


def _sign_in(client: TestClient, username: str) -> None:
    assert client.post(
        "/login",
        data={"username": username, "password": "password123"},
        follow_redirects=False,
    ).status_code == 303


def _authorize_params(response: httpx2.Response) -> dict[str, str]:
    assert response.status_code == 302, response.status_code
    return dict(parse_qsl(urlsplit(response.headers["location"]).query))


def _start_login(client: TestClient, provider: FakeProvider, next_path: str = "/") -> dict[str, str]:
    response = client.get(f"/auth/oidc/login?next={quote(next_path, safe='')}", follow_redirects=False)
    params = _authorize_params(response)
    provider.nonce = params.get("nonce", "")
    return params


def _callback(client: TestClient, state: str, **extra: str) -> httpx2.Response:
    query = "&".join(f"{key}={quote(value, safe='')}" for key, value in {"code": "provider-code", "state": state, **extra}.items())
    return client.get(f"/auth/oidc/callback?{query}", follow_redirects=False)


def _login(client: TestClient, provider: FakeProvider, next_path: str = "/") -> httpx2.Response:
    params = _start_login(client, provider, next_path)
    return _callback(client, params["state"])


# --- authorization request -------------------------------------------------


def test_login_start_uses_pkce_state_nonce_and_the_fixed_callback(oidc_app, provider):
    _db, client = oidc_app

    params = _start_login(client, provider)

    assert params["response_type"] == "code"
    assert params["client_id"] == CLIENT_ID
    assert params["scope"] == "openid profile email"
    assert params["redirect_uri"] == "https://testserver/auth/oidc/callback"
    assert params["code_challenge_method"] == "S256"
    assert params["code_challenge"]
    assert len(params["state"]) >= 32
    assert len(params["nonce"]) >= 16


def test_login_start_ignores_oidc_parameters_from_the_query_string(oidc_app, provider):
    _db, client = oidc_app

    response = client.get(
        "/auth/oidc/login?scope=openid+admin&redirect_uri=https://evil.example&prompt=none&client_id=other",
        follow_redirects=False,
    )
    params = _authorize_params(response)

    assert params["scope"] == "openid profile email"
    assert params["redirect_uri"] == "https://testserver/auth/oidc/callback"
    assert params["client_id"] == CLIENT_ID
    assert "prompt" not in params


def test_login_start_does_nothing_while_authentication_is_disabled(oidc_app, provider):
    db, client = oidc_app
    save_setting(db, "auth.enabled", "false")
    db.commit()

    response = client.get("/auth/oidc/login", follow_redirects=False)

    assert response.headers["location"] == "/"
    assert provider.token_requests == []


def test_login_start_does_nothing_while_single_sign_on_is_off(oidc_app, provider):
    db, client = oidc_app
    save_setting(db, OIDC_ENABLED_SETTING, "false")
    db.commit()

    response = client.get("/auth/oidc/login", follow_redirects=False)

    assert response.headers["location"] == "/login?oidc_error=unavailable"
    assert provider.token_requests == []


def test_login_routes_require_the_validated_https_boundary(oidc_app, provider):
    _db, _client = oidc_app
    foreign = TestClient(app, base_url="https://other.example")
    try:
        start = foreign.get("/auth/oidc/login", follow_redirects=False)
        callback = foreign.get("/auth/oidc/callback?code=a&state=b", follow_redirects=False)
    finally:
        foreign.close()

    assert start.status_code == 403
    assert callback.status_code == 403


# --- successful sign-in ----------------------------------------------------


def test_a_linked_identity_signs_in_and_gets_a_normal_session(oidc_app, provider):
    db, client = oidc_app

    response = _login(client, provider)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    session = db.query(UserSession).one()
    linked = db.query(User).filter(User.username == "linked").one()
    assert session.user_id == linked.id
    assert session.auth_method == AUTH_METHOD_OIDC
    assert linked.last_login_at is not None
    assert db.query(ExternalIdentity).one().last_login_at is not None


def test_the_token_exchange_sends_the_pkce_verifier(oidc_app, provider):
    _db, client = oidc_app

    _login(client, provider)

    assert len(provider.token_requests) == 1
    exchange = provider.token_requests[0]
    assert exchange["grant_type"] == "authorization_code"
    assert exchange["code"] == "provider-code"
    assert exchange["code_verifier"]
    assert exchange["redirect_uri"] == "https://testserver/auth/oidc/callback"


def test_the_local_role_stays_the_only_authorization_source(oidc_app, provider):
    db, client = oidc_app
    provider.claim_overrides = {"role": "admin", "groups": ["administrators"]}

    _login(client, provider)

    assert db.query(User).filter(User.username == "linked").one().role == "viewer"
    assert client.get("/settings", follow_redirects=False).status_code == 403


def test_sign_in_returns_to_a_validated_local_page(oidc_app, provider):
    _db, client = oidc_app

    response = _login(client, provider, next_path="/assets")

    assert response.headers["location"] == "/assets"


@pytest.mark.parametrize("target", ["https://evil.example/steal", "//evil.example/steal", "/\\evil.example"])
def test_sign_in_cannot_be_redirected_off_the_dashboard(oidc_app, provider, target):
    _db, client = oidc_app

    response = _login(client, provider, next_path=target)

    assert response.headers["location"] == "/"


# --- rejected sign-ins -----------------------------------------------------


def test_an_unknown_identity_is_rejected_without_creating_anything(oidc_app, provider):
    db, client = oidc_app
    provider.claim_overrides = {"sub": "0000-subject-unknown"}

    response = _login(client, provider)

    assert response.headers["location"] == "/login?oidc_error=login_failed"
    assert db.query(User).count() == 2
    assert db.query(ExternalIdentity).count() == 1
    assert db.query(UserSession).count() == 0


def test_a_matching_username_or_email_never_links_an_existing_account(oidc_app, provider):
    db, client = oidc_app
    create_user(db, "provider-person", "password123", "admin")
    db.commit()
    provider.claim_overrides = {
        "sub": "0000-subject-unknown",
        "preferred_username": "provider-person",
        "email": "provider-person@idp.example.test",
    }

    response = _login(client, provider)

    assert response.headers["location"] == "/login?oidc_error=login_failed"
    assert db.query(ExternalIdentity).count() == 1
    assert db.query(UserSession).count() == 0


def test_an_inactive_local_user_cannot_sign_in(oidc_app, provider):
    db, client = oidc_app
    db.query(User).filter(User.username == "linked").one().is_active = False
    db.commit()

    response = _login(client, provider)

    assert response.headers["location"] == "/login?oidc_error=login_failed"
    assert db.query(UserSession).count() == 0


def test_a_provider_error_response_is_rejected(oidc_app, provider):
    db, client = oidc_app
    params = _start_login(client, provider)

    response = _callback(client, params["state"], error="access_denied")

    assert response.headers["location"] == "/login?oidc_error=provider_error"
    assert db.query(UserSession).count() == 0


@pytest.mark.parametrize("state", ["", "not-the-state"])
def test_a_missing_or_wrong_state_is_rejected(oidc_app, provider, state):
    db, client = oidc_app
    _start_login(client, provider)

    response = _callback(client, state)

    assert response.headers["location"] == "/login?oidc_error=session_expired"
    assert db.query(UserSession).count() == 0


def test_a_replayed_state_is_rejected(oidc_app, provider):
    db, client = oidc_app
    params = _start_login(client, provider)
    assert _callback(client, params["state"]).status_code == 303

    replay = _callback(client, params["state"])

    assert replay.headers["location"] == "/login?oidc_error=session_expired"
    assert db.query(UserSession).count() == 1


def test_a_callback_without_a_started_flow_is_rejected(oidc_app, provider):
    _db, client = oidc_app

    response = _callback(client, "some-state")

    assert response.headers["location"] == "/login?oidc_error=session_expired"


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"nonce": "a-different-nonce"}, id="wrong-nonce"),
        pytest.param({"nonce": DROP}, id="missing-nonce"),
        pytest.param({"iss": "https://other.example.test"}, id="wrong-issuer"),
        pytest.param({"iss": DROP}, id="missing-issuer"),
        pytest.param({"aud": "another-client"}, id="wrong-audience"),
        pytest.param({"aud": [CLIENT_ID], "azp": "another-client"}, id="wrong-azp"),
        pytest.param({"exp": int(time.time()) - 600}, id="expired"),
        pytest.param({"nbf": int(time.time()) + 600}, id="not-yet-valid"),
        pytest.param({"sub": DROP}, id="missing-subject"),
        pytest.param({"sub": ""}, id="empty-subject"),
        pytest.param({"sub": "s" * 300}, id="oversized-subject"),
        pytest.param({"sub": 12345}, id="non-string-subject"),
    ],
)
def test_unusable_id_token_claims_are_rejected(oidc_app, provider, overrides):
    db, client = oidc_app
    provider.claim_overrides = overrides

    response = _login(client, provider)

    assert response.headers["location"] == "/login?oidc_error=provider_error"
    assert db.query(UserSession).count() == 0


@pytest.mark.parametrize("algorithm", ["none", "HS256"])
def test_unsafe_id_token_signatures_are_rejected(oidc_app, provider, algorithm):
    db, client = oidc_app
    provider.algorithm = algorithm

    response = _login(client, provider)

    assert response.headers["location"] == "/login?oidc_error=provider_error"
    assert db.query(UserSession).count() == 0


def test_an_unpublished_signing_key_is_rejected(oidc_app, provider):
    db, client = oidc_app
    provider.signing_key = RSAKey.generate_key(2048, parameters={"kid": "rotated-key", "use": "sig", "alg": "RS256"})

    response = _login(client, provider)

    assert response.headers["location"] == "/login?oidc_error=provider_error"
    assert db.query(UserSession).count() == 0


def test_a_rotated_signing_key_is_accepted_once_the_provider_publishes_it(oidc_app, provider):
    db, client = oidc_app
    assert _login(client, provider).status_code == 303
    rotated = RSAKey.generate_key(2048, parameters={"kid": "rotated-key", "use": "sig", "alg": "RS256"})
    provider.signing_key = rotated
    provider.published_keys = KeySet([rotated])

    response = _login(client, provider)

    # The key set is read per sign-in, so a rotation needs no restart - and the
    # tokens of neither sign-in are kept anywhere.
    assert response.headers["location"] == "/"
    assert db.query(UserSession).count() == 2
    assert ACCESS_TOKEN not in "\n".join(setting.value for setting in db.query(Setting).all())


def _break_endpoint(provider: FakeProvider, monkeypatch, path: str, response: httpx.Response) -> None:
    """Let one provider endpoint answer with something broken."""
    answer = provider._handle

    def broken(request: httpx.Request) -> httpx.Response:
        return response if request.url.path == path else answer(request)

    monkeypatch.setattr(provider, "_handle", broken)


@pytest.mark.parametrize("path", ["/token", "/jwks"])
def test_an_oversized_provider_answer_never_reaches_the_process(oidc_app, provider, monkeypatch, path):
    db, client = oidc_app
    oversized = httpx.Response(
        200,
        content=b"x" * (oidc.MAX_PROVIDER_RESPONSE_BYTES + 1),
        headers={"content-type": "application/json"},
    )
    _break_endpoint(provider, monkeypatch, path, oversized)

    response = _login(client, provider)

    assert response.headers["location"] == "/login?oidc_error=provider_error"
    assert db.query(UserSession).count() == 0


@pytest.mark.parametrize(
    "path,payload",
    [
        pytest.param("/token", 7, id="token-number"),
        pytest.param("/token", [], id="token-list"),
        pytest.param("/token", {"access_token": []}, id="token-wrong-field-type"),
        pytest.param("/jwks", 7, id="jwks-number"),
        pytest.param("/jwks", [], id="jwks-list"),
        pytest.param("/jwks", {"keys": 7}, id="jwks-wrong-field-type"),
    ],
)
def test_valid_json_with_the_wrong_shape_is_a_failed_sign_in(oidc_app, provider, monkeypatch, path, payload):
    db, client = oidc_app
    _break_endpoint(provider, monkeypatch, path, httpx.Response(200, json=payload))

    response = _login(client, provider)

    # A provider answer the parser cannot work with is a failed sign-in, never
    # an error page.
    assert response.status_code == 303
    assert response.headers["location"] == "/login?oidc_error=provider_error"
    assert db.query(UserSession).count() == 0


def test_only_safe_algorithms_reach_the_token_verification():
    assert safe_id_token_algorithms({"id_token_signing_alg_values_supported": ["none", "HS256", "ES256"]}) == ["ES256"]
    assert safe_id_token_algorithms({"id_token_signing_alg_values_supported": ["none", "HS256"]}) == ["RS256"]
    assert safe_id_token_algorithms({}) == ["RS256"]


# --- account linking -------------------------------------------------------


def test_a_signed_in_user_can_link_their_own_account(oidc_app, provider):
    db, client = oidc_app
    _sign_in(client, "admin")
    provider.claim_overrides = {"sub": OTHER_SUBJECT}

    start = client.post("/account/oidc/link", follow_redirects=False)
    params = _authorize_params(start)
    provider.nonce = params["nonce"]
    response = _callback(client, params["state"])

    assert response.headers["location"] == "/account?oidc_notice=linked"
    admin = db.query(User).filter(User.username == "admin").one()
    identity = db.query(ExternalIdentity).filter(ExternalIdentity.user_id == admin.id).one()
    assert (identity.issuer, identity.subject) == (ISSUER, OTHER_SUBJECT)
    assert admin.role == "admin"


def _link(client: TestClient, provider: FakeProvider) -> httpx2.Response:
    start = client.post("/account/oidc/link", follow_redirects=False)
    params = _authorize_params(start)
    provider.nonce = params["nonce"]
    return _callback(client, params["state"])


def test_linking_the_same_identity_twice_is_idempotent(oidc_app, provider):
    db, client = oidc_app
    _sign_in(client, "linked")

    response = _link(client, provider)

    assert response.headers["location"] == "/account?oidc_notice=already_linked"
    assert db.query(ExternalIdentity).count() == 1


def test_an_identity_of_another_user_cannot_be_taken_over(oidc_app, provider):
    db, client = oidc_app
    _sign_in(client, "admin")

    response = _link(client, provider)

    assert response.headers["location"] == "/account?oidc_error=identity_taken"
    admin = db.query(User).filter(User.username == "admin").one()
    assert db.query(ExternalIdentity).filter(ExternalIdentity.user_id == admin.id).count() == 0


def test_a_second_provider_account_does_not_replace_the_linked_one(oidc_app, provider):
    db, client = oidc_app
    _sign_in(client, "linked")
    provider.claim_overrides = {"sub": OTHER_SUBJECT}

    response = _link(client, provider)

    assert response.headers["location"] == "/account?oidc_error=other_identity"
    assert db.query(ExternalIdentity).one().subject == SUBJECT


def test_linking_fails_without_the_matching_local_session(oidc_app, provider):
    db, client = oidc_app
    _sign_in(client, "admin")
    provider.claim_overrides = {"sub": OTHER_SUBJECT}
    start = client.post("/account/oidc/link", follow_redirects=False)
    params = _authorize_params(start)
    provider.nonce = params["nonce"]
    client.cookies.delete("osd_session")

    response = _callback(client, params["state"])

    assert response.headers["location"] == "/account?oidc_error=session_expired"
    assert db.query(ExternalIdentity).count() == 1


def test_linking_never_changes_the_local_username_or_password(oidc_app, provider):
    db, client = oidc_app
    _sign_in(client, "admin")
    admin = db.query(User).filter(User.username == "admin").one()
    password_hash = admin.password_hash
    provider.claim_overrides = {"sub": OTHER_SUBJECT, "preferred_username": "provider-name"}

    _link(client, provider)
    db.refresh(admin)

    assert admin.username == "admin"
    assert admin.password_hash == password_hash


def test_the_link_start_needs_a_signed_in_user(oidc_app, provider):
    _db, client = oidc_app

    response = client.post("/account/oidc/link", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")
    assert provider.token_requests == []


# --- transaction cookie ----------------------------------------------------


def test_the_state_cookie_carries_the_agreed_protections(oidc_app, provider):
    _db, client = oidc_app

    response = client.get("/auth/oidc/login", follow_redirects=False)
    cookie = response.headers["set-cookie"]

    assert cookie.startswith(f"{OIDC_STATE_COOKIE}=")
    assert "path=/" in cookie
    assert "Max-Age=600" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "secure" in cookie


def test_the_state_cookie_is_cleared_after_the_callback(oidc_app, provider):
    _db, client = oidc_app

    response = _login(client, provider)

    assert f"{OIDC_STATE_COOKIE}=null" in response.headers["set-cookie"]
    assert client.cookies.get(OIDC_STATE_COOKIE) is None


def test_normal_pages_never_set_the_state_cookie(oidc_app, provider):
    _db, client = oidc_app
    _sign_in(client, "admin")

    for path in ("/", "/account", "/login"):
        response = client.get(path, follow_redirects=False)
        assert OIDC_STATE_COOKIE not in response.headers.get("set-cookie", "")


def test_the_state_cookie_never_carries_provider_material(oidc_app, provider):
    _db, client = oidc_app

    response = client.get("/auth/oidc/login", follow_redirects=False)
    payload = response.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]
    decoded = base64.b64decode(payload.split(".", 1)[0] + "==").decode("utf-8", "replace")

    assert CLIENT_SECRET not in decoded
    assert ACCESS_TOKEN not in decoded
    assert "osd_session" not in decoded


# --- nothing leaks ---------------------------------------------------------


def test_provider_tokens_are_never_stored_or_logged(oidc_app, provider, caplog):
    db, client = oidc_app
    with caplog.at_level(logging.DEBUG):
        _login(client, provider)

    stored = "\n".join(setting.value for setting in db.query(Setting).all())
    identity = db.query(ExternalIdentity).one()

    assert ACCESS_TOKEN not in stored
    assert provider.id_token not in stored
    assert ACCESS_TOKEN not in caplog.text
    assert provider.id_token not in caplog.text
    assert identity.subject == SUBJECT
    assert db.query(UserSession).one().auth_method == AUTH_METHOD_OIDC


def test_a_rejected_sign_in_leaks_nothing_to_the_browser_or_the_log(oidc_app, provider, caplog):
    _db, client = oidc_app
    provider.claim_overrides = {"sub": "0000-subject-unknown", "email": "someone@idp.example.test"}
    with caplog.at_level(logging.DEBUG):
        response = _login(client, provider)
    page = client.get(response.headers["location"])

    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    for secret in (ACCESS_TOKEN, "0000-subject-unknown", "someone@idp.example.test", CLIENT_SECRET):
        assert secret not in page.text
        assert secret not in caplog.text


def test_the_debug_report_stays_free_of_provider_material(oidc_app, provider):
    _db, client = oidc_app
    _login(client, provider)
    admin_client = TestClient(app, base_url="https://testserver")
    try:
        _sign_in(admin_client, "admin")
        report = admin_client.get("/diagnostics/debug-report")
    finally:
        admin_client.close()

    with zipfile.ZipFile(io.BytesIO(report.content)) as archive:
        body = "\n".join(archive.read(name).decode("utf-8", "replace") for name in archive.namelist())

    for secret in (ACCESS_TOKEN, SUBJECT, CLIENT_SECRET, provider.id_token):
        assert secret not in body


# --- user interface --------------------------------------------------------


def test_the_login_page_offers_single_sign_on_only_while_it_is_enabled(oidc_app, provider):
    db, client = oidc_app

    enabled = client.get("/login")
    save_setting(db, OIDC_ENABLED_SETTING, "false")
    db.commit()
    disabled = client.get("/login")

    assert "/auth/oidc/login" in enabled.text
    assert "Sign in with single sign-on" in enabled.text
    assert "/auth/oidc/login" not in disabled.text
    assert 'name="password"' in disabled.text


def test_the_login_page_shows_a_generic_single_sign_on_error(oidc_app, provider):
    _db, client = oidc_app

    page = client.get("/login?oidc_error=login_failed")

    assert "This provider account cannot sign in to OpenSecDash." in page.text


def test_the_new_strings_exist_in_german_too(oidc_app, provider):
    db, client = oidc_app
    # The login page follows the global language, the account page the
    # personal preference of the signed-in user.
    save_setting(db, "language", "de")
    admin = db.query(User).filter(User.username == "admin").one()
    db.query(UserPreference).filter(UserPreference.user_id == admin.id).one().language = "de"
    db.commit()
    _sign_in(client, "admin")

    login_page = TestClient(app, base_url="https://testserver").get("/login")
    account_page = client.get("/account")

    assert "Mit zentraler Anmeldung anmelden" in login_page.text
    assert "Anbieterkonto verknüpfen" in account_page.text
    assert "Noch nicht verknüpft" in account_page.text


def test_the_account_page_shows_the_link_state_without_provider_details(oidc_app, provider):
    _db, client = oidc_app
    _sign_in(client, "linked")

    page = client.get("/account")

    assert "Linked with the provider" in page.text
    assert "Link provider account" not in page.text
    assert SUBJECT not in page.text
    assert ISSUER not in page.text


def test_the_account_page_offers_linking_to_an_unlinked_user(oidc_app, provider):
    _db, client = oidc_app
    _sign_in(client, "admin")

    page = client.get("/account")

    assert "Not linked yet" in page.text
    assert 'action="/account/oidc/link"' in page.text


def test_the_password_form_is_hidden_for_a_user_without_a_local_password(oidc_app, provider):
    db, client = oidc_app
    _sign_in(client, "linked")
    with_password = client.get("/account")
    db.query(User).filter(User.username == "linked").one().password_hash = None
    db.commit()
    without_password = client.get("/account")

    assert 'action="/auth/password"' in with_password.text
    assert 'action="/auth/password"' not in without_password.text


# --- logout contract -------------------------------------------------------


def test_logout_only_ends_the_local_session(oidc_app, provider):
    db, client = oidc_app
    _login(client, provider)

    response = client.post("/auth/logout", follow_redirects=False)

    assert response.headers["location"] == "/login"
    assert db.query(UserSession).count() == 0
    assert db.query(ExternalIdentity).count() == 1
    assert db.query(Setting).filter(Setting.key.like("%id_token%")).count() == 0


def test_the_event_stream_works_with_a_provider_session_and_closes_after_revocation(oidc_app, provider):
    db, client = oidc_app
    save_setting(db, "plugin.traefik_log.enabled", "true")
    db.commit()
    _login(client, provider)

    with client.websocket_connect("wss://testserver/ws/events") as websocket:
        assert websocket.receive_json()["type"] == "connected"
        db.query(UserSession).delete()
        db.commit()

        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert exc_info.value.code == 1008


def test_password_sign_in_keeps_working_next_to_single_sign_on(oidc_app, provider):
    db, client = oidc_app

    _sign_in(client, "admin")

    assert db.query(UserSession).one().auth_method == AUTH_METHOD_PASSWORD
