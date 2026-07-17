import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.websockets import WebSocketDisconnect

from app.api import auth as auth_api
from app.database.dependencies import get_db
from app.database.base import Base
from app.main import app
from app.models.settings import Setting
from app.services.auth import create_user
from app.web import auth as auth_web


@pytest.fixture()
def auth_client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'auth-gating.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db_session = session_factory()

    def get_test_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(auth_web, "SessionLocal", session_factory)
    monkeypatch.setattr("app.main.SessionLocal", session_factory)
    monkeypatch.setattr("app.main.init_db", lambda: None)
    app.dependency_overrides[get_db] = get_test_db
    auth_api.reset_login_backoff()
    client = TestClient(app, base_url="https://testserver")
    try:
        yield db_session, client
    finally:
        client.close()
        app.dependency_overrides.clear()
        auth_api.reset_login_backoff()
        db_session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def enable_auth(db_session):
    db_session.add_all([Setting(key="auth.enabled", value="true"), Setting(key="auth.hostname", value="testserver")])
    user = create_user(db_session, "admin", "password123", "admin")
    db_session.commit()
    return user


def test_auth_gating_keeps_required_paths_public_and_rejects_anonymous_requests(auth_client):
    db_session, client = auth_client
    enable_auth(db_session)
    db_session.add(Setting(key="language", value="de"))
    db_session.commit()

    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=%2F"

    response = client.get("/api/events")
    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}

    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200
    assert client.get("/static/css/app.css").status_code == 200
    response = client.get("/login")
    assert response.status_code == 200
    assert '<html lang="de">' in response.text


def test_active_auth_rejects_wrong_proxy_origin_but_keeps_health_public(auth_client):
    db_session, client = auth_client
    enable_auth(db_session)

    assert client.get("/login", headers={"x-forwarded-host": "other.example"}).status_code == 403
    assert client.get("/login", headers={"x-forwarded-proto": "http", "x-forwarded-host": "testserver"}).status_code == 403
    assert client.get("/login", headers={"x-forwarded-host": "testserver:8443"}).status_code == 403
    assert client.get("/health", headers={"x-forwarded-proto": "http", "x-forwarded-host": "other.example"}).status_code == 200
    assert client.get("/ready", headers={"x-forwarded-proto": "http", "x-forwarded-host": "other.example"}).status_code == 200


def test_diagnostics_show_sanitized_auth_transport_status(auth_client):
    db_session, client = auth_client
    db_session.add(Setting(key="auth.hostname", value="testserver"))
    db_session.commit()

    page = client.get("/diagnostics")

    assert page.status_code == 200
    assert 'id="auth-transport"' in page.text
    assert "Internal user management" in page.text
    assert "Authentication transport" in page.text
    assert 'data-tooltip="This check is only relevant when internal user management is being enabled or is already enabled.' in page.text
    auth_transport_section = page.text.split('<section id="auth-transport"', maxsplit=1)[1].split("</section>", maxsplit=1)[0]
    assert "Internal user management" in auth_transport_section
    assert auth_transport_section.count("data-tooltip=") == 1
    assert 'data-tooltip="Configuration status: whether a plugin is enabled in Settings' in page.text
    assert 'data-tooltip="Runtime health: whether an enabled plugin is currently running without errors.' in page.text
    assert 'data-tooltip="Runtime status of datasource plugins' in page.text
    assert 'data-tooltip="Shows manually triggered actions' in page.text
    assert '<p class="muted mb-3 text-sm">Shows the newest 20 manual actions.</p>' in page.text
    diagnostics_results = page.text.split('<div id="diagnostics-results"', maxsplit=1)[1]
    assert diagnostics_results.count("data-tooltip=") == 5
    assert "The stored authentication hostname matches" in page.text
    assert "127.0.0.1" not in page.text


def test_existing_auth_without_hostname_fails_closed_until_break_glass(auth_client, monkeypatch):
    db_session, client = auth_client
    db_session.add(Setting(key="auth.enabled", value="true"))
    db_session.commit()

    assert client.get("/login").status_code == 403
    monkeypatch.setenv("OSD_AUTH_DISABLED", "true")
    assert client.get("/settings").status_code == 200


def test_auth_gating_uses_the_application_database_override(auth_client, monkeypatch):
    _, client = auth_client
    empty_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    monkeypatch.setattr(auth_web, "SessionLocal", sessionmaker(autocommit=False, autoflush=False, bind=empty_engine))
    try:
        assert client.get("/settings").status_code == 200
    finally:
        empty_engine.dispose()


def test_login_logout_and_cookie_flags_gate_browser_requests(auth_client):
    db_session, client = auth_client
    enable_auth(db_session)

    response = client.post("/login", data={"username": "admin", "password": "password123", "next": "/"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    assert "Secure" in response.headers["set-cookie"]
    assert response.headers["strict-transport-security"] == "max-age=31536000"

    response = client.get("/")
    assert response.status_code == 200
    assert "Cache-Control" in response.headers
    assert "no-store" in response.headers["Cache-Control"]
    assert response.headers["strict-transport-security"] == "max-age=31536000"
    assert "admin" in response.text

    response = client.post("/auth/logout", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert "osd_session=\"\"" in response.headers["set-cookie"]
    assert client.get("/", follow_redirects=False).status_code == 303


def test_login_backoff_and_open_redirect_protection(auth_client):
    db_session, client = auth_client
    enable_auth(db_session)

    for _ in range(5):
        response = client.post("/login", data={"username": "admin", "password": "wrong-password"}, follow_redirects=False)
        assert response.status_code == 401
        assert "Wrong username or password." in response.text

    response = client.post("/login", data={"username": "admin", "password": "password123"}, follow_redirects=False)
    assert response.status_code == 429
    assert "Too many failed attempts." in response.text

    auth_api.reset_login_backoff()
    for target in (
        "https://evil.example",
        "//evil.example",
        r"/\evil.example",
        r"/\\evil.example",
        r"\evil.example",
        r"https:\evil.example",
    ):
        response = client.post(
            "/login",
            data={"username": "admin", "password": "password123", "next": target},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/"
        client.post("/auth/logout", follow_redirects=False)


def test_login_backoff_cannot_be_bypassed_with_rotating_forwarded_ips(auth_client):
    db_session, client = auth_client
    enable_auth(db_session)

    for index in range(5):
        response = client.post(
            "/login",
            headers={"x-forwarded-for": f"203.0.113.{index + 1}"},
            data={"username": "admin", "password": "wrong-password"},
            follow_redirects=False,
        )
        assert response.status_code == 401

    response = client.post(
        "/login",
        headers={"x-forwarded-for": "203.0.113.99"},
        data={"username": "admin", "password": "password123"},
        follow_redirects=False,
    )

    assert response.status_code == 429


def test_login_backoff_limits_password_spraying_from_one_proxy_peer(auth_client, monkeypatch):
    db_session, client = auth_client
    enable_auth(db_session)
    monkeypatch.setattr(auth_api, "_MAX_SOURCE_LOGIN_FAILURES", 3)

    for index in range(3):
        response = client.post(
            "/login",
            headers={"x-forwarded-for": f"203.0.113.{index + 1}"},
            data={"username": f"unknown-{index}", "password": "wrong-password"},
            follow_redirects=False,
        )
        assert response.status_code == 401

    response = client.post(
        "/login",
        headers={"x-forwarded-for": "203.0.113.99"},
        data={"username": "admin", "password": "password123"},
        follow_redirects=False,
    )

    assert response.status_code == 429


def test_login_backoff_state_is_bounded_and_uses_fixed_size_keys(monkeypatch):
    auth_api.reset_login_backoff()
    monkeypatch.setattr(auth_api, "_MAX_LOGIN_BACKOFF_ENTRIES", 3)

    for index in range(4):
        auth_api._record_failed_login("x" * 10_000 + str(index), "192.0.2.1")

    assert len(auth_api._LOGIN_BACKOFF) == 3
    assert all(len(key) == 64 for key in auth_api._LOGIN_BACKOFF)


def test_origin_check_and_break_glass(auth_client, monkeypatch):
    db_session, client = auth_client
    enable_auth(db_session)
    client.post("/login", data={"username": "admin", "password": "password123"}, follow_redirects=False)

    response = client.post("/settings", headers={"origin": "https://evil.example"}, follow_redirects=False)
    assert response.status_code == 403
    response = client.post("/settings", headers={"origin": "http://testserver"}, follow_redirects=False)
    assert response.status_code == 403
    response = client.post("/settings", headers={"origin": "null"}, follow_redirects=False)
    assert response.status_code == 403
    response = client.post("/settings", headers={"sec-fetch-site": "cross-site"}, follow_redirects=False)
    assert response.status_code == 403
    response = client.post("/settings", headers={"origin": "https://testserver"}, follow_redirects=False)
    assert response.status_code != 403
    response = client.post("/settings", headers={"origin": "https://testserver:443"}, follow_redirects=False)
    assert response.status_code != 403
    response = client.post("/login", headers={"origin": "https://evil.example"}, follow_redirects=False)
    assert response.status_code == 403
    client.headers.pop("origin")
    response = client.post("/settings", follow_redirects=False)
    assert response.status_code == 403
    response = client.post("/settings", headers={"referer": "https://evil.example/settings"}, follow_redirects=False)
    assert response.status_code == 403
    response = client.post("/settings", headers={"referer": "https://testserver/settings"}, follow_redirects=False)
    assert response.status_code != 403
    client.headers["origin"] = "https://testserver"

    client.post("/auth/logout", follow_redirects=False)
    monkeypatch.setenv("OSD_AUTH_DISABLED", "true")
    assert client.get("/", follow_redirects=False).status_code == 200


def test_websocket_closes_anonymous_connections_when_auth_is_enabled(auth_client):
    db_session, client = auth_client
    enable_auth(db_session)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/events"):
            pass

    assert exc_info.value.code == 1008


def test_authenticated_websocket_requires_the_configured_proxy_origin(auth_client):
    db_session, client = auth_client
    enable_auth(db_session)
    db_session.add(Setting(key="plugin.traefik_log.enabled", value="true"))
    db_session.commit()
    client.post("/login", data={"username": "admin", "password": "password123"}, follow_redirects=False)

    with client.websocket_connect("wss://testserver/ws/events") as websocket:
        assert websocket.receive_json()["type"] == "connected"

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("wss://testserver/ws/events", headers={"x-forwarded-host": "other.example"}):
            pass

    assert exc_info.value.code == 1008
