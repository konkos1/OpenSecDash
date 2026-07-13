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
from app.models.users import User, UserSession
from app.services.auth import create_session, create_user
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
        data={"username": "admin", "password": "password123", "password_confirm": "password123"},
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
    assert db.query(User).filter(User.username == "admin", User.role == "admin").count() == 1
    assert client.get("/settings").status_code == 200


def test_activation_without_admin_or_when_environment_disabled_is_rejected(user_management_client, monkeypatch):
    db, client = user_management_client

    response = client.post("/settings/auth/enable", follow_redirects=False)
    assert response.headers["location"] == "/settings?auth_error=no_admin"
    assert get_setting_value(db, "auth.enabled", "false") == "false"

    monkeypatch.setenv("OSD_AUTH_DISABLED", "true")
    response = client.post(
        "/settings/auth/enable",
        data={"username": "admin", "password": "password123", "password_confirm": "password123"},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/settings?auth_error=env_disabled"


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
        assert client.post(f"/settings/users/{viewer.id}/password", data={"password": "newpassword123"}, follow_redirects=False).status_code == 303
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


def test_disabling_authentication_removes_sessions_and_opens_app(user_management_client):
    db, client = user_management_client
    _enable_auth(client)
    assert db.query(UserSession).count() == 1

    response = client.post("/settings/auth/disable", follow_redirects=False)

    assert response.status_code == 303
    assert get_setting_value(db, "auth.enabled", "true") == "false"
    assert db.query(UserSession).count() == 0
    anonymous = TestClient(app, base_url="https://testserver")
    try:
        assert anonymous.get("/").status_code == 200
    finally:
        anonymous.close()


def test_account_password_change_replaces_sessions(user_management_client):
    db, client = user_management_client
    _enable_auth(client)
    admin = db.query(User).filter(User.username == "admin").one()
    other_token = create_session(db, admin)
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
