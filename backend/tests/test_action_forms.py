from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.database.dependencies import get_db
from app.main import app
from app.models.core import Action
from app.models.events import Event
from app.models import *  # noqa: F403 - import models so SQLAlchemy registers all tables
from app.models.settings import Setting


@pytest.fixture()
def action_db(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'actions.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = session_factory()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _client(db):
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_action_form_creates_dry_run_ban(action_db):
    client = _client(action_db)
    try:
        response = client.post(
            "/actions/ip",
            data={"action_type": "security.ban", "ip": "8.8.8.8", "duration": "24h", "confirmed": "true"},
            follow_redirects=False,
        )
    finally:
        client.close()
        app.dependency_overrides.clear()

    assert response.status_code == 303
    assert response.headers["location"] == "/ip/8.8.8.8"
    action = action_db.query(Action).one()
    assert action.status == "completed"
    assert action.parameters["duration"] == "24h"
    assert action.parameters["reason"].startswith("Manual ban via OpenSecDash")
    assert action_db.query(Event).filter_by(event_type="security.ban.manual").count() == 1


def test_action_form_records_failed_private_ip(action_db):
    client = _client(action_db)
    try:
        response = client.post(
            "/actions/ip",
            data={"action_type": "security.ban", "ip": "192.168.1.1", "duration": "4h", "confirmed": "true"},
            follow_redirects=False,
        )
    finally:
        client.close()
        app.dependency_overrides.clear()

    assert response.status_code == 303
    assert action_db.query(Action).one().status == "failed"
    assert action_db.query(Event).filter_by(event_type="action.failed").count() == 1


def test_action_form_records_unknown_action_failure(action_db):
    client = _client(action_db)
    try:
        response = client.post(
            "/actions/ip",
            data={"action_type": "unknown.action", "ip": "8.8.8.8", "confirmed": "true"},
            follow_redirects=False,
        )
    finally:
        client.close()
        app.dependency_overrides.clear()

    assert response.status_code == 303
    action = action_db.query(Action).one()
    assert action.status == "failed"
    assert action.plugin_id == "core"


def test_action_form_accepts_crowdsec_alias(action_db):
    client = _client(action_db)
    try:
        response = client.post(
            "/actions/ip",
            data={"action_type": "crowdsec_ban", "ip": "8.8.8.8", "duration": "4h", "confirmed": "true"},
            follow_redirects=False,
        )
    finally:
        client.close()
        app.dependency_overrides.clear()

    assert response.status_code == 303
    action = action_db.query(Action).one()
    assert action.action_type == "crowdsec_ban"
    assert action.status == "completed"


def test_action_form_preserves_invalid_declared_parameter(action_db):
    client = _client(action_db)
    try:
        response = client.post(
            "/actions/ip",
            data={"action_type": "security.ban", "ip": "8.8.8.8", "duration": "12h", "confirmed": "true"},
            follow_redirects=False,
        )
    finally:
        client.close()
        app.dependency_overrides.clear()

    assert response.status_code == 303
    action = action_db.query(Action).one()
    assert action.status == "failed"
    assert action.parameters["duration"] == "12h"


def test_ip_action_panel_is_registry_driven(action_db):
    action_db.add(Setting(key="plugin.crowdsec.enabled", value="true"))
    action_db.add(Setting(key="action_dry_run", value="true"))
    action_db.commit()

    client = _client(action_db)
    try:
        response = client.get("/ip/1.2.3.4")
    finally:
        client.close()
        app.dependency_overrides.clear()

    assert response.status_code == 200
    html = response.text
    assert "CrowdSec Ban" in html
    assert "Unban IP" in html
    assert all(option in html for option in ("value=\"4h\"", "value=\"24h\"", "value=\"7d\""))
    assert html.count('action="/actions/ip"') == 2
    assert html.count("data-confirm=") == 2
    assert html.count("ip.actions") == 0
