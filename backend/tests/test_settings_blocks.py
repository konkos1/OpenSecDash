from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import auth as auth_api
from app.database.base import Base
from app.database.dependencies import get_db
from app.main import app
from app.models.settings import Setting
from app.web import auth as auth_web


class _FormNestingParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.form_depth = 0
        self.has_nested_form = False

    def handle_starttag(self, tag, attrs):
        if tag == "form":
            self.has_nested_form = self.has_nested_form or self.form_depth > 0
            self.form_depth += 1

    def handle_endtag(self, tag):
        if tag == "form":
            self.form_depth -= 1


@pytest.fixture()
def settings_client(tmp_path: Path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'settings-blocks.db'}", connect_args={"check_same_thread": False})
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


def test_settings_blocks_are_independent_and_use_non_nested_forms(settings_client):
    db, client = settings_client
    db.add_all(
        [
            Setting(key="domain", value="before.example"),
            Setting(key="notifications.enabled", value="false"),
            Setting(key="asset_updates.github_interval", value="21600"),
            Setting(key="plugin.crowdsec.enabled", value="true"),
        ]
    )
    db.commit()
    page = client.get("/settings")
    core_response = client.post("/settings/core", data={"domain": "after.example"}, follow_redirects=False)
    notification_response = client.post("/settings/notifications", data={"notifications_enabled": "true"}, follow_redirects=False)
    asset_response = client.post("/settings/asset-updates", data={"asset_updates_github_interval": "7200"}, follow_redirects=False)

    parser = _FormNestingParser()
    parser.feed(page.text)
    assert parser.has_nested_form is False
    assert core_response.status_code == 303
    assert notification_response.status_code == 303
    assert asset_response.status_code == 303
    assert db.query(Setting).filter_by(key="domain").one().value == "after.example"
    assert db.query(Setting).filter_by(key="notifications.enabled").one().value == "true"
    assert db.query(Setting).filter_by(key="asset_updates.github_interval").one().value == "7200"
    assert db.query(Setting).filter_by(key="plugin.crowdsec.enabled").one().value == "true"
    assert 'action="/settings"' not in page.text
    assert 'name="language"' not in page.text
    assert 'name="live_default"' not in page.text
    assert 'name="theme"' not in page.text
    assert 'name="instance_accent_color"' not in page.text
    assert 'name="live_page_refresh"' not in page.text


def test_settings_details_open_only_core_and_place_users_after_branding(settings_client):
    _, client = settings_client
    page = client.get("/settings")

    assert page.text.count('<details class="card mb-5" open>') == 1
    assert page.text.index("Instance Branding") < page.text.index("Sign-in &amp; users")
