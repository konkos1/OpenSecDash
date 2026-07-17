from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import auth as auth_api
from app.database.base import Base
from app.database.dependencies import get_db
from app.main import app
from app.models.saved_views import SavedView
from app.models.settings import Setting
from app.models.users import User
from app.services.auth import create_user
from app.web import auth as auth_web
from app.web.auth import required_role
from app.web.dashboard import dashboard_layout_setting_key, load_dashboard_layout


@pytest.fixture()
def role_clients(tmp_path: Path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'auth-roles.db'}", connect_args={"check_same_thread": False})
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
    db.add_all(
        [
            Setting(key="auth.enabled", value="true"),
            Setting(key="auth.hostname", value="testserver"),
            Setting(key="plugin.traefik_log.enabled", value="true"),
            Setting(key="plugin.json_assets.enabled", value="true"),
        ]
    )
    for role in ("viewer", "operator", "admin"):
        create_user(db, role, "password123", role)
    db.commit()

    clients = {role: TestClient(app, base_url="https://testserver") for role in ("viewer", "operator", "admin")}
    try:
        for role, client in clients.items():
            response = client.post("/login", data={"username": role, "password": "password123"}, follow_redirects=False)
            assert response.status_code == 303
        yield db, clients
    finally:
        for client in clients.values():
            client.close()
        app.dependency_overrides.clear()
        auth_api.reset_login_backoff()
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.mark.parametrize(
    ("method", "path", "role"),
    [
        ("GET", "/", "viewer"),
        ("POST", "/api/actions", "operator"),
        ("POST", "/events/columns", "viewer"),
        ("GET", "/settings", "admin"),
        ("GET", "/api/settings", "admin"),
        ("GET", "/diagnostics/debug-report", "admin"),
        ("POST", "/auth/password", "viewer"),
        ("POST", "/account/preferences", "viewer"),
        ("POST", "/irgendwas/neues", "operator"),
        ("POST", "/views", "viewer"),
        ("POST", "/dashboard/layout", "viewer"),
    ],
)
def test_required_role_matrix(method, path, role):
    assert required_role(method, path) == role


def test_viewer_can_read_and_use_viewer_preferences(role_clients):
    _, clients = role_clients
    viewer = clients["viewer"]

    assert viewer.get("/").status_code == 200
    assert viewer.get("/events").status_code == 200
    assert viewer.post("/api/actions", json={}).json() == {"detail": "Forbidden"}
    response = viewer.get("/settings")
    assert response.status_code == 403
    assert 'href="/settings"' not in response.text
    assert viewer.post("/events/columns", data={"snapshot_before": ""}, follow_redirects=False).status_code == 303
    assert viewer.post("/views", data={"scope": "events", "name": "My view"}, follow_redirects=False).status_code == 303
    assert viewer.post("/dashboard/layout", follow_redirects=False).status_code == 303


def test_operator_can_operate_but_cannot_access_settings(role_clients):
    _, clients = role_clients
    operator = clients["operator"]

    assert operator.post("/api/actions", json={}).status_code != 403
    assert operator.get("/settings").status_code == 403
    assert operator.get("/api/settings").json() == {"detail": "Forbidden"}


def test_admin_can_access_and_save_settings(role_clients):
    _, clients = role_clients
    admin = clients["admin"]

    assert admin.get("/settings").status_code == 200
    assert admin.post("/settings/core", follow_redirects=False).status_code == 303


def test_settings_navigation_is_hidden_for_viewers(role_clients):
    _, clients = role_clients

    assert 'href="/settings"' not in clients["viewer"].get("/").text
    assert 'href="/settings"' in clients["admin"].get("/").text


def test_auth_disabled_keeps_settings_and_action_controls_visible(role_clients):
    db, clients = role_clients
    db.query(Setting).filter(Setting.key == "auth.enabled").delete()
    db.commit()

    dashboard = clients["viewer"].get("/")
    assets = clients["viewer"].get("/assets")

    assert 'href="/settings"' in dashboard.text
    assert 'action="/assets/refresh-updates"' in assets.text


def test_saved_views_are_isolated_per_authenticated_user(role_clients):
    db, clients = role_clients
    viewer = db.query(User).filter(User.username == "viewer").one()
    operator = db.query(User).filter(User.username == "operator").one()

    assert clients["viewer"].post(
        "/views",
        data={"scope": "events", "name": "Errors", "filters": "status_min=400"},
        follow_redirects=False,
    ).status_code == 303
    assert clients["operator"].post(
        "/views",
        data={"scope": "events", "name": "Errors", "filters": "status_min=500"},
        follow_redirects=False,
    ).status_code == 303

    db.expire_all()
    assert db.query(SavedView).filter(SavedView.user_id == viewer.id).one().filter_json == {"status_code_min": 400}
    assert db.query(SavedView).filter(SavedView.user_id == operator.id).one().filter_json == {"status_code_min": 500}


def test_legacy_views_are_copied_once_for_each_authenticated_user(role_clients):
    db, clients = role_clients
    db.add(SavedView(name="Legacy", scope="events", filter_json={"country": "DE"}))
    db.commit()
    viewer = db.query(User).filter(User.username == "viewer").one()
    operator = db.query(User).filter(User.username == "operator").one()

    assert clients["viewer"].get("/events").status_code == 200
    assert clients["operator"].get("/events").status_code == 200

    db.expire_all()
    assert db.query(SavedView).filter(SavedView.user_id == viewer.id, SavedView.name == "Legacy").count() == 1
    assert db.query(SavedView).filter(SavedView.user_id == operator.id, SavedView.name == "Legacy").count() == 1


def test_dashboard_layouts_are_isolated_per_authenticated_user(role_clients, monkeypatch):
    db, clients = role_clients
    viewer = db.query(User).filter(User.username == "viewer").one()
    operator = db.query(User).filter(User.username == "operator").one()
    monkeypatch.setattr("app.api.pages.dashboard_layout_widget_ids", lambda db: {"first", "second"})

    assert clients["viewer"].post(
        "/dashboard/layout",
        data={"widget_id": ["first", "second"], "visible": ["first"]},
        follow_redirects=False,
    ).status_code == 303
    assert clients["operator"].post(
        "/dashboard/layout",
        data={"widget_id": ["second", "first"], "visible": ["second"]},
        follow_redirects=False,
    ).status_code == 303

    db.expire_all()
    assert load_dashboard_layout(db, viewer.id) == [{"id": "first", "visible": True}, {"id": "second", "visible": False}]
    assert load_dashboard_layout(db, operator.id) == [{"id": "second", "visible": True}, {"id": "first", "visible": False}]
    assert dashboard_layout_setting_key(viewer.id) != dashboard_layout_setting_key(operator.id)
