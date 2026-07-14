import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.template_context import get_setting_value
from app.api import pages
from app.database.base import Base
from app.database.dependencies import get_db
from app.main import app
from app.models.core import Notification, NotificationRule
from app.models.settings import Setting
from app.services.notifications import seed_default_notification_rules
from app.web.tables import save_setting


@pytest.fixture()
def test_settings_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'notification-settings.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = session_factory()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_seed_default_notification_rules_is_idempotent_and_preserves_user_changes(db_session):
    seed_default_notification_rules(db_session)
    db_session.commit()

    rules = db_session.query(NotificationRule).order_by(NotificationRule.rule_id).all()
    assert [rule.rule_id for rule in rules] == [
        "core.asset_offline",
        "core.crowdsec_ban",
        "core.plugin_error",
        "core.scanner_detected",
    ]
    assert rules[0].cooldown_minutes == 60
    assert rules[1].match_types == ["security.ban"]
    assert rules[3].source == "insight"

    crowdsec_rule = next(rule for rule in rules if rule.rule_id == "core.crowdsec_ban")
    crowdsec_rule.enabled = False
    db_session.commit()
    seed_default_notification_rules(db_session)
    db_session.commit()

    assert db_session.query(NotificationRule).count() == 4
    assert db_session.query(NotificationRule).filter_by(rule_id="core.crowdsec_ban").one().enabled is False


def test_notification_smtp_password_is_encrypted_at_rest(db_session):
    save_setting(db_session, "notifications.smtp_password", "geheim")
    db_session.commit()

    stored = db_session.query(Setting).filter_by(key="notifications.smtp_password").one()
    assert stored.value.startswith("enc:v1:")
    assert get_setting_value(db_session, "notifications.smtp_password") == "geheim"


def test_notification_settings_section_renders(test_settings_db):
    app.dependency_overrides[get_db] = lambda: test_settings_db
    client = TestClient(app)
    try:
        response = client.get("/settings")
    finally:
        client.close()
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert 'name="notifications_smtp_host"' in response.text


def test_notification_settings_post_persists_all_values(test_settings_db):
    app.dependency_overrides[get_db] = lambda: test_settings_db
    client = TestClient(app)
    try:
        response = client.post(
            "/settings/notifications",
            data={
                "notifications_enabled": "true",
                "notifications_base_url": "http://192.168.1.10:8000/",
                "notifications_smtp_host": "smtp.example.net",
                "notifications_smtp_port": "465",
                "notifications_smtp_security": "ssl",
                "notifications_smtp_user": "mailer",
                "notifications_smtp_password": "geheim",
                "notifications_smtp_sender": "opensecdash@example.net",
                "notifications_smtp_recipient": "admin@example.net",
            },
            follow_redirects=False,
        )
    finally:
        client.close()
        app.dependency_overrides.clear()

    assert response.status_code == 303
    assert response.headers["location"] == "/settings"
    expected = {
        "notifications.enabled": "true",
        "notifications.base_url": "http://192.168.1.10:8000/",
        "notifications.smtp_host": "smtp.example.net",
        "notifications.smtp_port": "465",
        "notifications.smtp_security": "ssl",
        "notifications.smtp_user": "mailer",
        "notifications.smtp_password": "geheim",
        "notifications.smtp_sender": "opensecdash@example.net",
        "notifications.smtp_recipient": "admin@example.net",
    }
    assert {key: get_setting_value(test_settings_db, key) for key in expected} == expected
    assert test_settings_db.query(Setting).filter_by(key="notifications.smtp_password").one().value.startswith("enc:v1:")


def test_notification_test_email_subject_includes_primary_domain(test_settings_db, monkeypatch):
    class FakeChannel:
        def __init__(self):
            self.subject = ""

        def is_configured(self, db):
            return True

        def send(self, db, subject, body, html_body=None):
            self.subject = subject

    channel = FakeChannel()
    monkeypatch.setattr(pages, "get_channel", lambda _: channel)
    save_setting(test_settings_db, "domain", "homelab.example")
    test_settings_db.commit()
    app.dependency_overrides[get_db] = lambda: test_settings_db
    client = TestClient(app)
    try:
        response = client.post("/notifications/test", follow_redirects=False)
    finally:
        client.close()
        app.dependency_overrides.clear()

    assert response.status_code == 303
    assert channel.subject == "[OpenSecDash] homelab.example · Notification test"
    assert test_settings_db.query(Notification).filter_by(rule_id="core.test", status="sent").count() == 1
