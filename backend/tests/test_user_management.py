from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import auth as auth_api
from app.core.template_context import get_setting_value
from app.database.base import Base
from app.database.dependencies import get_db
from app.main import app
from app.models.settings import Setting
from app.models.users import ExternalIdentity, User, UserPreference, UserSession
from app.services.auth import (
    AUTH_METHOD_OIDC,
    AUTH_METHOD_PASSWORD,
    create_session,
    create_user,
    link_external_identity,
)
from app.web import auth as auth_web


@pytest.fixture()
def user_management_client(tmp_path: Path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'user-management.db'}", connect_args={"check_same_thread": False})
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
    client = TestClient(app, base_url="https://testserver")
    try:
        yield db, client
    finally:
        client.close()
        app.dependency_overrides.clear()
        auth_api.reset_login_backoff()
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _enable_auth(client: TestClient):
    response = client.post(
        "/settings/auth/enable",
        data={"hostname": "testserver", "username": "admin", "password": "password123", "password_confirm": "password123"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return response


def _login(client: TestClient, username: str, password: str):
    response = client.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert response.status_code == 303
    return response


def test_activation_creates_admin_session_and_allows_settings(user_management_client):
    db, client = user_management_client

    response = _enable_auth(client)

    assert "osd_session=" in response.headers["set-cookie"]
    assert get_setting_value(db, "auth.enabled", "false") == "true"
    assert get_setting_value(db, "auth.hostname", "") == "testserver"
    assert db.query(User).filter(User.username == "admin", User.role == "admin").count() == 1
    assert client.get("/settings").status_code == 200


@pytest.mark.parametrize(
    ("base_url", "headers", "hostname", "error"),
    [
        ("https://testserver", {}, "other.example", "hostname_mismatch"),
        ("http://testserver", {}, "testserver", "https_required"),
        ("https://testserver:8443", {}, "testserver", "https_port_required"),
        ("https://testserver", {}, "https://testserver", "invalid_hostname"),
    ],
)
def test_activation_requires_trusted_https_443_proxy_and_matching_hostname(
    user_management_client,
    base_url,
    headers,
    hostname,
    error,
):
    db, _client = user_management_client
    client = TestClient(app, base_url=base_url, headers=headers)
    try:
        response = client.post(
            "/settings/auth/enable",
            data={"hostname": hostname, "username": "admin", "password": "password123", "password_confirm": "password123"},
            follow_redirects=False,
        )
    finally:
        client.close()

    assert response.headers["location"] == f"/settings?auth_error={error}"
    assert get_setting_value(db, "auth.enabled", "false") == "false"
    assert db.query(User).count() == 0


def test_activation_rejects_a_peer_outside_explicit_proxy_trust(user_management_client):
    db, _client = user_management_client
    client = TestClient(app, base_url="https://testserver", client=("203.0.113.10", 50000))
    try:
        response = client.post(
            "/settings/auth/enable",
            data={"hostname": "testserver", "username": "admin", "password": "password123", "password_confirm": "password123"},
            follow_redirects=False,
        )
    finally:
        client.close()

    assert response.headers["location"] == "/settings?auth_error=proxy_not_configured"
    assert get_setting_value(db, "auth.enabled", "false") == "false"
    assert db.query(User).count() == 0


def test_activation_requires_forwarded_port_443(user_management_client):
    db, client = user_management_client
    client.headers.pop("x-forwarded-port")

    response = client.post(
        "/settings/auth/enable",
        data={"hostname": "testserver", "username": "admin", "password": "password123", "password_confirm": "password123"},
        follow_redirects=False,
    )

    assert response.headers["location"] == "/settings?auth_error=https_port_required"
    assert get_setting_value(db, "auth.enabled", "false") == "false"
    assert db.query(User).count() == 0


def test_proxy_activation_error_links_to_diagnostics(user_management_client):
    _db, client = user_management_client

    response = client.post(
        "/settings/auth/enable",
        data={"hostname": "other.example", "username": "admin", "password": "password123", "password_confirm": "password123"},
        follow_redirects=False,
    )
    page = client.get(response.headers["location"])

    assert page.status_code == 200
    assert "secure proxy boundary is incomplete" in page.text
    assert 'href="/diagnostics#auth-transport"' in page.text
    assert 'data-tooltip="The DNS hostname used to reach OpenSecDash over HTTPS on port 443.' in page.text


def test_cross_site_activation_is_rejected_when_authentication_is_disabled(user_management_client):
    db, client = user_management_client
    credentials = {"hostname": "testserver", "username": "attacker", "password": "password123", "password_confirm": "password123"}

    for headers in ({"origin": "https://evil.example"}, {"sec-fetch-site": "cross-site"}):
        response = client.post(
            "/settings/auth/enable",
            data=credentials,
            headers=headers,
            follow_redirects=False,
        )

        assert response.status_code == 403
    client.headers.pop("origin")
    response = client.post("/settings/auth/enable", data=credentials, follow_redirects=False)

    assert response.status_code == 403
    assert get_setting_value(db, "auth.enabled", "false") == "false"
    assert db.query(User).count() == 0


def test_core_preferences_are_hidden_and_unchanged_when_authentication_is_enabled(user_management_client):
    db, client = user_management_client
    db.add_all(
        [
            Setting(key="language", value="en"),
            Setting(key="live_default", value="true"),
            Setting(key="theme", value="auto"),
            Setting(key="instance_accent_color", value="blue"),
            Setting(key="live_page_refresh", value="true"),
        ]
    )
    db.commit()
    _enable_auth(client)
    admin = db.query(User).filter(User.username == "admin").one()
    admin_preferences = db.query(UserPreference).filter(UserPreference.user_id == admin.id).one()
    admin_preferences.theme = "dark"
    admin_preferences.accent_color = "red"
    db.commit()

    page = client.get("/settings")
    response = client.post(
        "/settings/core",
        data={
            "language": "de",
            "live_default": "false",
            "theme": "light",
            "accent_color": "green",
            "live_page_refresh": "false",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert 'name="language"' not in page.text
    assert 'name="live_default"' not in page.text
    assert 'name="theme"' not in page.text
    assert 'name="accent_color"' not in page.text
    assert 'name="live_page_refresh"' not in page.text
    assert 'data-theme="dark"' in page.text
    assert 'data-accent="red"' in page.text
    assert get_setting_value(db, "language", "") == "en"
    assert get_setting_value(db, "live_default", "") == "true"
    assert get_setting_value(db, "theme", "") == "auto"
    assert get_setting_value(db, "instance_accent_color", "") == "blue"
    assert get_setting_value(db, "live_page_refresh", "") == "true"


def test_activation_without_admin_or_when_environment_disabled_is_rejected(user_management_client, monkeypatch):
    db, client = user_management_client

    response = client.post("/settings/auth/enable", data={"hostname": "testserver"}, follow_redirects=False)
    assert response.headers["location"] == "/settings?auth_error=no_admin"
    assert get_setting_value(db, "auth.enabled", "false") == "false"

    monkeypatch.setenv("OSD_AUTH_DISABLED", "true")
    response = client.post(
        "/settings/auth/enable",
        data={"hostname": "testserver", "username": "admin", "password": "password123", "password_confirm": "password123"},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/settings?auth_error=env_disabled"


def test_break_glass_can_repair_hostname_and_revokes_sessions(user_management_client, monkeypatch):
    db, client = user_management_client
    _enable_auth(client)
    assert db.query(UserSession).count() == 1
    monkeypatch.setenv("OSD_AUTH_DISABLED", "true")

    page = client.get("/settings")
    response = client.post("/settings/auth/hostname", data={"hostname": "new.example"}, follow_redirects=False)

    assert page.status_code == 200
    assert 'action="/settings/auth/hostname"' in page.text
    assert 'data-tooltip="The DNS hostname used to reach OpenSecDash over HTTPS on port 443.' in page.text
    assert response.headers["location"] == "/settings?auth_notice=hostname_saved"
    assert get_setting_value(db, "auth.hostname", "") == "new.example"
    assert db.query(UserSession).count() == 0


def test_admin_can_manage_users_and_revokes_target_sessions(user_management_client):
    db, client = user_management_client
    _enable_auth(client)
    for username, role in (("viewer", "viewer"), ("operator", "operator"), ("otheradmin", "admin")):
        response = client.post(
            "/settings/users/create",
            data={"username": username, "password": "password123", "role": role},
            follow_redirects=False,
        )
        assert response.status_code == 303

    viewer = db.query(User).filter(User.username == "viewer").one()
    operator = db.query(User).filter(User.username == "operator").one()
    viewer_client = TestClient(app, base_url="https://testserver")
    operator_client = TestClient(app, base_url="https://testserver")
    try:
        _login(viewer_client, "viewer", "password123")
        _login(operator_client, "operator", "password123")
        assert client.post(f"/settings/users/{viewer.id}/role", data={"role": "operator"}, follow_redirects=False).status_code == 303
        assert client.post("/settings/users/password", data={"user_id": viewer.id, "password": "newpassword123"}, follow_redirects=False).status_code == 303
        assert viewer_client.get("/", follow_redirects=False).status_code == 303

        assert client.post(f"/settings/users/{operator.id}/toggle", follow_redirects=False).status_code == 303
        assert operator_client.get("/", follow_redirects=False).status_code == 303
        assert client.post(f"/settings/users/{operator.id}/delete", follow_redirects=False).status_code == 303
        assert db.query(User).filter(User.id == operator.id).first() is None
    finally:
        viewer_client.close()
        operator_client.close()


def test_last_admin_and_self_delete_protections(user_management_client):
    db, client = user_management_client
    _enable_auth(client)
    admin = db.query(User).filter(User.username == "admin").one()

    for path, data in (
        (f"/settings/users/{admin.id}/role", {"role": "viewer"}),
        (f"/settings/users/{admin.id}/toggle", {}),
    ):
        response = client.post(path, data=data, follow_redirects=False)
        assert response.headers["location"] == "/settings?auth_error=last_admin"
    assert client.post(f"/settings/users/{admin.id}/delete", follow_redirects=False).headers["location"] == "/settings?auth_error=self_delete"
    assert db.query(User).filter(User.id == admin.id, User.role == "admin", User.is_active == True).count() == 1  # noqa: E712

    second_admin = create_user(db, "secondadmin", "password123", "admin")
    db.commit()
    response = client.post(f"/settings/users/{second_admin.id}/delete", follow_redirects=False)
    assert response.status_code == 303
    assert db.query(User).filter(User.id == second_admin.id).first() is None
    assert db.query(UserPreference).filter(UserPreference.user_id == second_admin.id).count() == 0


def test_deleting_a_user_removes_sessions_preferences_and_external_identities(user_management_client):
    db, client = user_management_client
    _enable_auth(client)
    linked_admin = create_user(db, "linkedadmin", "password123", "admin")
    db.flush()
    link_external_identity(db, linked_admin.id, "https://idp.example/realms/homelab", "subject-1")
    create_session(db, linked_admin, AUTH_METHOD_OIDC)
    db.commit()

    response = client.post(f"/settings/users/{linked_admin.id}/delete", follow_redirects=False)

    assert response.status_code == 303
    assert db.query(User).filter(User.id == linked_admin.id).first() is None
    assert db.query(UserSession).filter(UserSession.user_id == linked_admin.id).count() == 0
    assert db.query(UserPreference).filter(UserPreference.user_id == linked_admin.id).count() == 0
    assert db.query(ExternalIdentity).filter(ExternalIdentity.user_id == linked_admin.id).count() == 0


def test_operator_and_viewer_cannot_manage_users(user_management_client):
    db, client = user_management_client
    _enable_auth(client)
    operator = create_user(db, "operator", "password123", "operator")
    viewer = create_user(db, "viewer", "password123", "viewer")
    db.commit()
    operator_client = TestClient(app, base_url="https://testserver")
    viewer_client = TestClient(app, base_url="https://testserver")
    try:
        _login(operator_client, "operator", "password123")
        _login(viewer_client, "viewer", "password123")
        assert operator_client.post(f"/settings/users/{operator.id}/toggle").status_code == 403
        assert viewer_client.post(f"/settings/users/{viewer.id}/toggle").status_code == 403
    finally:
        operator_client.close()
        viewer_client.close()


def test_account_password_change_replaces_sessions(user_management_client):
    db, client = user_management_client
    _enable_auth(client)
    admin = db.query(User).filter(User.username == "admin").one()
    other_token = create_session(db, admin, AUTH_METHOD_PASSWORD)
    db.commit()
    other_client = TestClient(app, base_url="https://testserver")
    other_client.cookies.set("osd_session", other_token)
    try:
        response = client.post(
            "/auth/password",
            data={"current_password": "wrong", "new_password": "newpassword123", "new_password_confirm": "newpassword123"},
            follow_redirects=False,
        )
        assert response.headers["location"] == "/account?auth_error=wrong_password"

        response = client.post(
            "/auth/password",
            data={"current_password": "password123", "new_password": "newpassword123", "new_password_confirm": "newpassword123"},
            follow_redirects=False,
        )
        assert response.headers["location"] == "/account?auth_notice=password_changed"
        assert client.get("/account").status_code == 200
        assert other_client.get("/", follow_redirects=False).status_code == 303

        old_login = TestClient(app, base_url="https://testserver")
        new_login = TestClient(app, base_url="https://testserver")
        try:
            assert old_login.post("/login", data={"username": "admin", "password": "password123"}, follow_redirects=False).status_code == 401
            _login(new_login, "admin", "newpassword123")
        finally:
            old_login.close()
            new_login.close()
    finally:
        other_client.close()


def test_account_redirects_to_dashboard_when_auth_is_disabled(user_management_client):
    _, client = user_management_client

    response = client.get("/account", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_every_role_can_update_only_its_own_preferences(user_management_client):
    db, admin_client = user_management_client
    _enable_auth(admin_client)
    account_html = admin_client.get("/account").text
    assert account_html.count('class="help" data-tooltip=') == 5
    assert "Changes the language shown in your interface." in account_html
    assert 'id="account-preferences-form"' in account_html
    assert 'hx-select="#account-preferences-form"' in account_html
    assert 'id="account-password-form"' in account_html
    assert 'hx-select="#account-password-form"' in account_html
    assert account_html.count('data-unsaved-warning="Discard unsaved settings changes?"') == 2
    assert account_html.count(" data-save-feedback ") == 2
    operator = create_user(db, "operator", "password123", "operator")
    viewer = create_user(db, "viewer", "password123", "viewer")
    db.add_all(
        [
            Setting(key="language", value="en"),
            Setting(key="live_default", value="true"),
            Setting(key="theme", value="auto"),
            Setting(key="instance_accent_color", value="blue"),
            Setting(key="live_page_refresh", value="true"),
        ]
    )
    db.commit()
    clients = {
        "admin": admin_client,
        "operator": TestClient(app, base_url="https://testserver"),
        "viewer": TestClient(app, base_url="https://testserver"),
    }
    try:
        _login(clients["operator"], "operator", "password123")
        _login(clients["viewer"], "viewer", "password123")
        for role, client in clients.items():
            response = client.post(
                "/account/preferences",
                data={
                    "language": "de" if role != "viewer" else "en",
                    "live_default": "false",
                    "theme": "dark" if role != "viewer" else "light",
                    "accent_color": "red" if role != "viewer" else "green",
                    "live_page_refresh": "false",
                },
                follow_redirects=False,
            )
            assert response.headers["location"] == "/account?auth_notice=preferences_saved"

        admin = db.query(User).filter(User.username == "admin").one()
        admin_preferences = db.query(UserPreference).filter(UserPreference.user_id == admin.id).one()
        operator_preferences = db.query(UserPreference).filter(UserPreference.user_id == operator.id).one()
        viewer_preferences = db.query(UserPreference).filter(UserPreference.user_id == viewer.id).one()
        assert (admin_preferences.language, admin_preferences.theme, admin_preferences.accent_color) == ("de", "dark", "red")
        assert (operator_preferences.language, operator_preferences.theme, operator_preferences.accent_color) == ("de", "dark", "red")
        assert (viewer_preferences.language, viewer_preferences.theme, viewer_preferences.accent_color) == ("en", "light", "green")
        assert db.query(Setting).filter(Setting.key == "theme", Setting.value == "auto").count() == 1
        assert 'data-theme="dark"' in clients["operator"].get("/account").text
        assert 'data-theme="light"' in clients["viewer"].get("/account").text
    finally:
        clients["operator"].close()
        clients["viewer"].close()


def test_preferences_reject_invalid_values_without_partial_update(user_management_client):
    db, client = user_management_client
    _enable_auth(client)
    admin = db.query(User).filter(User.username == "admin").one()
    preferences = db.query(UserPreference).filter(UserPreference.user_id == admin.id).one()
    original_values = (preferences.language, preferences.live_default, preferences.theme, preferences.accent_color, preferences.live_page_refresh)

    response = client.post(
        "/account/preferences",
        data={
            "language": "invalid",
            "live_default": "false",
            "theme": "dark",
            "accent_color": "red",
            "live_page_refresh": "false",
        },
        follow_redirects=False,
    )

    assert response.headers["location"] == "/account?auth_error=invalid_preferences"
    db.refresh(preferences)
    assert (preferences.language, preferences.live_default, preferences.theme, preferences.accent_color, preferences.live_page_refresh) == original_values


def test_preferences_require_an_authenticated_session(user_management_client):
    _, client = user_management_client
    _enable_auth(client)
    client.cookies.clear()

    response = client.post(
        "/account/preferences",
        data={
            "language": "en",
            "live_default": "true",
            "theme": "auto",
            "accent_color": "blue",
            "live_page_refresh": "true",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=%2Faccount%2Fpreferences"
