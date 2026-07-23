import asyncio
import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import auth as auth_api
from app.api import oidc_settings
from app.core.template_context import get_setting_value
from app.database.base import Base
from app.database.dependencies import get_db
from app.main import app
from app.models.settings import Setting
from app.models.users import UserSession
from app.services import oidc
from app.services.auth import AUTH_METHOD_OIDC, AUTH_METHOD_PASSWORD, create_session, create_user
from app.services.oidc import (
    OIDC_CHECK_STATUS_SETTING,
    OIDC_CLIENT_SECRET_SETTING,
    OIDC_JIT_ENABLED_SETTING,
    PASSWORD_LOGIN_ENABLED_SETTING,
    OidcConfigurationError,
    callback_url,
    check_provider,
    fetch_discovery_document,
    load_config,
    validate_discovery_metadata,
    validate_provider_url,
)
from app.web import auth as auth_web

ISSUER = "https://idp.example.test"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"


def _metadata(**overrides):
    document = {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "jwks_uri": f"{ISSUER}/jwks",
        "userinfo_endpoint": f"{ISSUER}/userinfo",
        "response_types_supported": ["code", "id_token"],
        "id_token_signing_alg_values_supported": ["RS256"],
    }
    document.update(overrides)
    return document


def _json_response(document, *, status_code: int = 200, content_type: str = "application/json") -> httpx.Response:
    return httpx.Response(status_code, content=json.dumps(document).encode(), headers={"content-type": content_type})


def _fetch(discovery_url: str, handler):
    return asyncio.run(fetch_discovery_document(discovery_url, transport=httpx.MockTransport(handler), resolve=False))


def _check(discovery_url: str, handler):
    return asyncio.run(check_provider(discovery_url, transport=httpx.MockTransport(handler), resolve=False))


def _error_code(callable_, *args, **kwargs) -> str:
    with pytest.raises(OidcConfigurationError) as raised:
        callable_(*args, **kwargs)
    return raised.value.code


# --- URL policy -----------------------------------------------------------


def test_private_homelab_provider_urls_are_allowed():
    assert validate_provider_url("https://10.10.0.5/.well-known/openid-configuration", resolve=False) == (
        "https://10.10.0.5/.well-known/openid-configuration"
    )
    assert validate_provider_url("https://[fd12:3456::20]/.well-known/openid-configuration", resolve=False) == (
        "https://[fd12:3456::20]/.well-known/openid-configuration"
    )


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("", "invalid_url"),
        ("not a url", "insecure_url"),
        ("https://idp.example.test:99999/x", "invalid_url"),
        ("http://idp.example.test/.well-known/openid-configuration", "insecure_url"),
        ("https://user:secret@idp.example.test/.well-known/openid-configuration", "url_not_plain"),
        ("https://idp.example.test/.well-known/openid-configuration?tenant=a", "url_not_plain"),
        ("https://idp.example.test/.well-known/openid-configuration#frag", "url_not_plain"),
        ("https://127.0.0.1/.well-known/openid-configuration", "blocked_url"),
        ("https://[::1]/.well-known/openid-configuration", "blocked_url"),
        ("https://localhost/.well-known/openid-configuration", "blocked_url"),
        ("https://169.254.169.254/.well-known/openid-configuration", "blocked_url"),
        ("https://169.254.10.10/.well-known/openid-configuration", "blocked_url"),
        ("https://metadata.google.internal/.well-known/openid-configuration", "blocked_url"),
    ],
)
def test_unsafe_provider_urls_are_rejected(url, code):
    assert _error_code(validate_provider_url, url, resolve=False) == code


def test_callback_url_comes_only_from_the_auth_hostname():
    assert callback_url("dash.example.test") == "https://dash.example.test/auth/oidc/callback"
    assert callback_url("https://dash.example.test") is None
    assert callback_url("") is None


# --- discovery metadata ---------------------------------------------------


def test_valid_discovery_document_yields_the_issuer():
    assert validate_discovery_metadata(_metadata(), DISCOVERY_URL, resolve=False) == ISSUER


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"token_endpoint": None}, "invalid_metadata"),
        ({"jwks_uri": ""}, "invalid_metadata"),
        ({"issuer": None}, "invalid_metadata"),
        ({"issuer": "https://other.example.test"}, "invalid_issuer"),
        ({"issuer": "http://idp.example.test"}, "invalid_issuer"),
        ({"jwks_uri": "http://idp.example.test/jwks"}, "blocked_endpoint"),
        ({"token_endpoint": "https://127.0.0.1/token"}, "blocked_endpoint"),
        ({"userinfo_endpoint": "https://169.254.169.254/userinfo"}, "blocked_endpoint"),
        ({"response_types_supported": ["id_token"]}, "unsupported_flow"),
        ({"response_types_supported": "code"}, "unsupported_flow"),
        ({"id_token_signing_alg_values_supported": ["HS256", "none"]}, "unsupported_algorithms"),
        ({"id_token_signing_alg_values_supported": []}, "unsupported_algorithms"),
    ],
)
def test_unsafe_discovery_documents_are_rejected(overrides, code):
    assert _error_code(validate_discovery_metadata, _metadata(**overrides), DISCOVERY_URL, resolve=False) == code


# --- discovery transport --------------------------------------------------


def test_discovery_is_fetched_and_checked():
    issuer, document = _check(DISCOVERY_URL, lambda request: _json_response(_metadata()))

    assert issuer == ISSUER
    assert document["token_endpoint"] == f"{ISSUER}/token"


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(404, content=b"{}", headers={"content-type": "application/json"}), "invalid_response"),
        (httpx.Response(200, content=b"<html></html>", headers={"content-type": "text/html"}), "invalid_response"),
        (httpx.Response(200, content=b"not json", headers={"content-type": "application/json"}), "invalid_response"),
        (httpx.Response(200, content=b"[]", headers={"content-type": "application/json"}), "invalid_response"),
    ],
)
def test_broken_discovery_responses_are_rejected(response, code):
    assert _error_code(_fetch, DISCOVERY_URL, lambda request: response) == code


def test_oversized_discovery_response_is_rejected():
    oversized = httpx.Response(
        200,
        content=b"x" * (oidc.MAX_METADATA_BYTES + 1),
        headers={"content-type": "application/json"},
    )
    assert _error_code(_fetch, DISCOVERY_URL, lambda request: oversized) == "response_too_large"


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError("dns failure"),
        httpx.ConnectTimeout("connect timeout"),
        httpx.ReadTimeout("read timeout"),
    ],
)
def test_unreachable_providers_report_one_sanitized_code(failure):
    def handler(request):
        raise failure

    assert _error_code(_fetch, DISCOVERY_URL, handler) == "unreachable"


def test_tls_failures_report_one_sanitized_code():
    def handler(request):
        raise httpx.ConnectError("certificate verify failed")

    assert _error_code(_fetch, DISCOVERY_URL, handler) == "unreachable"


def test_redirect_to_a_forbidden_target_is_rejected():
    def handler(request):
        return httpx.Response(302, headers={"location": "https://127.0.0.1/.well-known/openid-configuration"})

    assert _error_code(_fetch, DISCOVERY_URL, handler) == "blocked_redirect"


def test_redirect_to_plain_http_is_rejected():
    def handler(request):
        return httpx.Response(302, headers={"location": "http://idp.example.test/.well-known/openid-configuration"})

    assert _error_code(_fetch, DISCOVERY_URL, handler) == "blocked_redirect"


def test_redirect_loops_are_rejected():
    def handler(request):
        return httpx.Response(302, headers={"location": str(request.url)})

    assert _error_code(_fetch, DISCOVERY_URL, handler) == "blocked_redirect"


def test_allowed_redirect_targets_are_followed_once_revalidated():
    def handler(request):
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(302, headers={"location": f"{ISSUER}/openid-configuration"})
        return _json_response(_metadata())

    final_url, document = _fetch(DISCOVERY_URL, handler)

    assert final_url == f"{ISSUER}/openid-configuration"
    assert document["issuer"] == ISSUER


# --- settings routes ------------------------------------------------------


@pytest.fixture()
def oidc_client(tmp_path: Path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'oidc.db'}", connect_args={"check_same_thread": False})
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
    db.add_all([Setting(key="auth.enabled", value="true"), Setting(key="auth.hostname", value="testserver")])
    for role in ("viewer", "operator", "admin"):
        create_user(db, role, "password123", role)
    db.commit()
    oidc.invalidate_provider_cache()

    clients = {}
    try:
        for role in ("viewer", "operator", "admin"):
            client = TestClient(app, base_url="https://testserver")
            assert client.post("/login", data={"username": role, "password": "password123"}, follow_redirects=False).status_code == 303
            clients[role] = client
        yield db, clients
    finally:
        for client in clients.values():
            client.close()
        app.dependency_overrides.clear()
        auth_api.reset_login_backoff()
        oidc.invalidate_provider_cache()
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _stub_check(monkeypatch, *, issuer: str = ISSUER, error: str | None = None):
    calls: list[str] = []

    async def fake_check(discovery_url, **kwargs):
        calls.append(discovery_url)
        if error is not None:
            raise OidcConfigurationError(error)
        return issuer, _metadata(issuer=issuer)

    monkeypatch.setattr(oidc_settings, "check_provider", fake_check)
    return calls


def _save_provider(client: TestClient, **data):
    payload = {"discovery_url": DISCOVERY_URL, "client_id": "dashboard-client-1234", "client_secret": "provider-secret"}
    payload.update(data)
    return client.post("/settings/auth/oidc", data=payload, follow_redirects=False)


def test_saving_a_provider_stores_the_configuration_and_the_check_result(oidc_client, monkeypatch):
    db, clients = oidc_client
    calls = _stub_check(monkeypatch)

    response = _save_provider(clients["admin"])

    assert response.headers["location"] == "/settings?oidc_notice=configuration_saved"
    assert calls == [DISCOVERY_URL]
    config = load_config(db)
    assert config.discovery_url == DISCOVERY_URL
    assert config.client_id == "dashboard-client-1234"
    assert config.client_secret == "provider-secret"
    assert config.issuer == ISSUER
    assert config.enabled is False
    assert get_setting_value(db, OIDC_CHECK_STATUS_SETTING, "") == "healthy"


def test_client_secret_is_stored_encrypted_and_never_rendered(oidc_client, monkeypatch):
    db, clients = oidc_client
    _stub_check(monkeypatch)
    _save_provider(clients["admin"])

    stored = db.query(Setting).filter(Setting.key == OIDC_CLIENT_SECRET_SETTING).one()
    assert stored.value.startswith("enc:v1:")
    assert "provider-secret" not in stored.value

    page = clients["admin"].get("/settings")
    assert page.status_code == 200
    assert "provider-secret" not in page.text
    assert "A secret is stored" in page.text


def test_empty_secret_field_keeps_the_stored_secret(oidc_client, monkeypatch):
    db, clients = oidc_client
    _stub_check(monkeypatch)
    _save_provider(clients["admin"])

    response = _save_provider(clients["admin"], client_id="renamed", client_secret="")

    assert response.headers["location"] == "/settings?oidc_notice=configuration_saved"
    config = load_config(db)
    assert config.client_id == "renamed"
    assert config.client_secret == "provider-secret"


def test_incomplete_configuration_is_rejected_without_contacting_the_provider(oidc_client, monkeypatch):
    db, clients = oidc_client
    calls = _stub_check(monkeypatch)

    response = _save_provider(clients["admin"], client_secret="")

    assert response.headers["location"] == "/settings?oidc_error=incomplete_config"
    assert calls == []
    assert load_config(db).discovery_url == ""


def test_a_failing_check_never_replaces_a_working_configuration(oidc_client, monkeypatch):
    db, clients = oidc_client
    _stub_check(monkeypatch)
    _save_provider(clients["admin"])
    _stub_check(monkeypatch, error="unreachable")

    response = _save_provider(clients["admin"], discovery_url="https://broken.example.test/.well-known/openid-configuration")

    assert response.headers["location"] == "/settings?oidc_error=unreachable"
    config = load_config(db)
    assert config.discovery_url == DISCOVERY_URL
    assert config.client_secret == "provider-secret"
    assert get_setting_value(db, OIDC_CHECK_STATUS_SETTING, "") == "healthy"


def test_enabling_requires_a_complete_configuration(oidc_client, monkeypatch):
    db, clients = oidc_client
    _stub_check(monkeypatch)

    response = clients["admin"].post("/settings/auth/oidc/enable", follow_redirects=False)

    assert response.headers["location"] == "/settings?oidc_error=incomplete_config"
    assert load_config(db).enabled is False


def test_enabling_requires_a_successful_live_check(oidc_client, monkeypatch):
    db, clients = oidc_client
    _stub_check(monkeypatch)
    _save_provider(clients["admin"])
    _stub_check(monkeypatch, error="blocked_endpoint")

    response = clients["admin"].post("/settings/auth/oidc/enable", follow_redirects=False)

    assert response.headers["location"] == "/settings?oidc_error=blocked_endpoint"
    assert load_config(db).enabled is False
    assert get_setting_value(db, OIDC_CHECK_STATUS_SETTING, "") == "error"


def test_enabling_rejects_a_changed_issuer(oidc_client, monkeypatch):
    db, clients = oidc_client
    _stub_check(monkeypatch)
    _save_provider(clients["admin"])
    _stub_check(monkeypatch, issuer="https://idp.example.test/other")

    response = clients["admin"].post("/settings/auth/oidc/enable", follow_redirects=False)

    assert response.headers["location"] == "/settings?oidc_error=invalid_issuer"
    assert load_config(db).enabled is False


def test_enabling_and_disabling_a_checked_provider(oidc_client, monkeypatch):
    db, clients = oidc_client
    _stub_check(monkeypatch)
    _save_provider(clients["admin"])

    assert clients["admin"].post("/settings/auth/oidc/enable", follow_redirects=False).headers["location"] == (
        "/settings?oidc_notice=enabled"
    )
    assert load_config(db).enabled is True

    response = clients["admin"].post("/settings/auth/oidc/disable", follow_redirects=False)

    assert response.headers["location"] == "/settings?oidc_notice=disabled"
    assert load_config(db).enabled is False


def test_disabling_revokes_only_oidc_sessions(oidc_client, monkeypatch):
    db, clients = oidc_client
    _stub_check(monkeypatch)
    _save_provider(clients["admin"])
    viewer = create_user(db, "linked", "password123", "viewer")
    db.flush()
    create_session(db, viewer, AUTH_METHOD_OIDC)
    create_session(db, viewer, AUTH_METHOD_PASSWORD)
    db.commit()

    clients["admin"].post("/settings/auth/oidc/disable", follow_redirects=False)

    assert db.query(UserSession).filter(UserSession.auth_method == AUTH_METHOD_OIDC).count() == 0
    assert db.query(UserSession).filter(UserSession.auth_method == AUTH_METHOD_PASSWORD, UserSession.user_id == viewer.id).count() == 1


def test_deleting_the_client_secret_disables_single_sign_on(oidc_client, monkeypatch):
    db, clients = oidc_client
    _stub_check(monkeypatch)
    _save_provider(clients["admin"])
    clients["admin"].post("/settings/auth/oidc/enable", follow_redirects=False)

    response = clients["admin"].post("/settings/auth/oidc/secret/delete", follow_redirects=False)

    assert response.headers["location"] == "/settings?oidc_notice=secret_deleted"
    config = load_config(db)
    assert config.client_secret == ""
    assert config.enabled is False
    assert config.complete is False
    assert config.discovery_url == DISCOVERY_URL


def test_jit_switch_defaults_to_off_and_can_be_saved(oidc_client):
    db, clients = oidc_client

    assert load_config(db).jit_enabled is False
    response = clients["admin"].post("/settings/auth/oidc/jit", data={"jit_enabled": "true"}, follow_redirects=False)

    assert response.headers["location"] == "/settings?oidc_notice=jit_saved"
    assert get_setting_value(db, OIDC_JIT_ENABLED_SETTING, "false") == "true"


def test_provider_configuration_is_locked_while_password_login_is_off(oidc_client, monkeypatch):
    db, clients = oidc_client
    _stub_check(monkeypatch)
    _save_provider(clients["admin"])
    # Password sign-in can only be off while a usable provider exists, so the
    # lock is only reachable with single sign-on actually enabled.
    clients["admin"].post("/settings/auth/oidc/enable", follow_redirects=False)
    db.add(Setting(key=PASSWORD_LOGIN_ENABLED_SETTING, value="false"))
    db.commit()

    saved = _save_provider(clients["admin"], discovery_url="https://other.example.test/.well-known/openid-configuration")
    deleted = clients["admin"].post("/settings/auth/oidc/secret/delete", follow_redirects=False)

    assert saved.headers["location"] == "/settings?oidc_error=password_login_locked"
    assert deleted.headers["location"] == "/settings?oidc_error=password_login_locked"
    config = load_config(db)
    assert config.discovery_url == DISCOVERY_URL
    assert config.client_secret == "provider-secret"


@pytest.mark.parametrize("role", ["viewer", "operator"])
@pytest.mark.parametrize(
    "path",
    [
        "/settings/auth/oidc",
        "/settings/auth/oidc/enable",
        "/settings/auth/oidc/disable",
        "/settings/auth/oidc/secret/delete",
        "/settings/auth/oidc/jit",
    ],
)
def test_only_admins_can_change_oidc_settings(oidc_client, role, path):
    db, clients = oidc_client

    response = clients[role].post(path, data={"discovery_url": DISCOVERY_URL}, follow_redirects=False)

    assert response.status_code == 403
    assert load_config(db).discovery_url == ""


def test_oidc_settings_require_the_trusted_https_boundary(oidc_client, monkeypatch):
    db, _clients = oidc_client
    _stub_check(monkeypatch)
    client = TestClient(app, base_url="https://other.example")
    try:
        response = _save_provider(client)
    finally:
        client.close()

    assert response.status_code == 403
    assert load_config(db).discovery_url == ""


def test_diagnostics_and_debug_report_stay_sanitized(oidc_client, monkeypatch):
    db, clients = oidc_client
    _stub_check(monkeypatch)
    _save_provider(clients["admin"])
    clients["admin"].post("/settings/auth/oidc/enable", follow_redirects=False)

    diagnostics = clients["admin"].get("/diagnostics")
    report = clients["admin"].get("/diagnostics/debug-report")

    assert diagnostics.status_code == 200
    assert "Single sign-on (OIDC)" in diagnostics.text
    assert "The discovery document was fetched and checked successfully." in diagnostics.text
    assert "Linked provider accounts" in diagnostics.text
    for secret in (DISCOVERY_URL, ISSUER, "dashboard-client-1234", "provider-secret"):
        assert secret not in diagnostics.text
    assert report.status_code == 200
    with zipfile.ZipFile(io.BytesIO(report.content)) as archive:
        body = "\n".join(archive.read(name).decode("utf-8", "replace") for name in archive.namelist())
    assert "OIDC discovery check: healthy" in body
    assert "OIDC enabled: True" in body
    for secret in (DISCOVERY_URL, ISSUER, "dashboard-client-1234", "provider-secret"):
        assert secret not in body


def test_sign_in_routes_do_nothing_while_the_provider_is_not_enabled(oidc_client):
    _db, clients = oidc_client

    response = clients["admin"].get("/auth/oidc/login", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?oidc_error=unavailable"


def test_provider_metadata_cache_is_invalidated_by_a_configuration_change():
    config = oidc.OidcConfig(
        enabled=True,
        discovery_url=DISCOVERY_URL,
        client_id="dashboard-client-1234",
        client_secret="provider-secret",
        jit_enabled=False,
        issuer=ISSUER,
    )
    oidc.store_provider_metadata(config, _metadata())

    assert oidc.cached_provider_metadata(config) is not None
    changed = oidc.OidcConfig(**{**config.__dict__, "client_id": "other"})
    assert oidc.cached_provider_metadata(changed) is None

    oidc.invalidate_provider_cache()
    assert oidc.cached_provider_metadata(config) is None


def test_authlib_client_uses_pkce_and_the_openid_scopes():
    config = oidc.OidcConfig(
        enabled=True,
        discovery_url=DISCOVERY_URL,
        client_id="dashboard-client-1234",
        client_secret="provider-secret",
        jit_enabled=False,
        issuer=ISSUER,
    )

    client = oidc.build_oauth_client(config, _metadata())

    assert client.client_kwargs["scope"] == "openid profile email"
    assert client.client_kwargs["code_challenge_method"] == "S256"
    assert client.client_kwargs["follow_redirects"] is False
    assert client.server_metadata["issuer"] == ISSUER
