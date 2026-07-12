from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import pages
from app.core.time import utc_now
from app.database.base import Base
from app.database.dependencies import get_db
from app.main import app
from app.models.core import Notification, NotificationRule
from app.services.events import store_event
from app.services.notifications import invalidate_rules_cache, seed_default_notification_rules
from app.web.tables import save_setting


@pytest.fixture()
def notifications_db(tmp_path, _test_secret_key):
    engine = create_engine(f"sqlite:///{tmp_path / 'notifications.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    seed_default_notification_rules(db)
    db.add(Notification(rule_id="core.crowdsec_ban", channel="email", status="failed", error="SMTP unavailable"))
    db.commit()
    invalidate_rules_cache()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _client(db):
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_notifications_page_shows_rules_history_and_configuration_hint(notifications_db):
    client = _client(notifications_db)
    try:
        response = client.get("/notifications")
    finally:
        client.close()
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "CrowdSec ban" in response.text
    assert "Send test" in response.text
    assert "SMTP unavailable" in response.text
    assert "Notifications are disabled or SMTP is not configured." in response.text
    assert 'href="/notifications"' in response.text
    assert 'action="/notifications/test" data-submit-busy' in response.text
    assert 'data-busy-label="Processing…"' in response.text


def test_notification_rule_toggle_invalidates_engine_cache(notifications_db):
    save_setting(notifications_db, "notifications.enabled", "true")
    notifications_db.commit()
    invalidate_rules_cache()
    client = _client(notifications_db)
    try:
        response = client.post("/notifications/rules", data={"rule_id": "core.scanner_detected"}, follow_redirects=False)
    finally:
        client.close()
        app.dependency_overrides.clear()

    assert response.status_code == 303
    assert notifications_db.query(NotificationRule).filter_by(rule_id="core.crowdsec_ban").one().enabled is False
    store_event(
        notifications_db,
        source="test",
        plugin="crowdsec",
        event_type="security.ban",
        severity="warning",
        event_time=utc_now().replace(tzinfo=None),
        ip="198.51.100.44",
    )
    notifications_db.flush()
    assert notifications_db.query(Notification).filter_by(rule_id="core.crowdsec_ban", status="pending").count() == 0


def test_notifications_page_uses_german_translation(notifications_db):
    save_setting(notifications_db, "language", "de")
    notifications_db.commit()
    client = _client(notifications_db)
    try:
        response = client.get("/notifications")
    finally:
        client.close()
        app.dependency_overrides.clear()

    assert "Benachrichtigungen" in response.text
