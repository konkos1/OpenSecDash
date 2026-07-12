from datetime import timedelta

import pytest

from app.core.time import utc_now
from app.models.core import Insight, Notification, NotificationRule
from app.models.events import Event
from app.plugins.manager import get_plugin_manager
from app.services.events import store_event
from app.services.notifications import handle_insight, invalidate_rules_cache, seed_default_notification_rules
from app.web.tables import save_setting


@pytest.fixture(autouse=True)
def notification_rules(db_session, _test_secret_key):
    invalidate_rules_cache()
    seed_default_notification_rules(db_session)
    save_setting(db_session, "notifications.enabled", "true")
    db_session.commit()
    yield
    invalidate_rules_cache()


def _notifications(db_session, rule_id: str) -> list[Notification]:
    db_session.flush()
    return db_session.query(Notification).filter(Notification.rule_id == rule_id).all()


def test_fresh_crowdsec_ban_queues_pending_notification(db_session):
    event = store_event(
        db_session,
        source="test",
        plugin="crowdsec",
        event_type="security.ban",
        severity="warning",
        event_time=utc_now().replace(tzinfo=None),
        ip="198.51.100.1",
    )

    notifications = _notifications(db_session, "core.crowdsec_ban")
    assert len(notifications) == 1
    assert notifications[0].status == "pending"
    assert notifications[0].payload is not None
    assert notifications[0].payload["event_id"] == event.id


def test_old_event_does_not_queue_notification(db_session):
    store_event(
        db_session,
        source="test",
        plugin="crowdsec",
        event_type="security.ban",
        severity="warning",
        event_time=utc_now().replace(tzinfo=None) - timedelta(minutes=30),
        ip="198.51.100.2",
    )

    db_session.flush()
    assert db_session.query(Notification).count() == 0


def test_disabled_master_switch_does_not_queue_notification(db_session):
    save_setting(db_session, "notifications.enabled", "false")
    db_session.commit()
    invalidate_rules_cache()

    store_event(
        db_session,
        source="test",
        plugin="crowdsec",
        event_type="security.ban",
        severity="warning",
        event_time=utc_now().replace(tzinfo=None),
        ip="198.51.100.3",
    )

    db_session.flush()
    assert db_session.query(Notification).count() == 0


def test_event_matching_honors_severity_country_and_wildcard(db_session):
    db_session.add_all(
        [
            NotificationRule(
                rule_id="test.error_ru",
                name="Error from Russia",
                source="event",
                match_types=["access.error"],
                min_severity="error",
                countries=["RU"],
            ),
            NotificationRule(
                rule_id="test.system_wildcard",
                name="System event",
                source="event",
                match_types=["system.*"],
                min_severity="info",
            ),
        ]
    )
    db_session.commit()
    invalidate_rules_cache()

    store_event(
        db_session,
        source="test",
        plugin="test",
        event_type="access.error",
        severity="warning",
        event_time=utc_now().replace(tzinfo=None),
        country="RU",
        ip="198.51.100.4",
    )
    store_event(
        db_session,
        source="test",
        plugin="test",
        event_type="access.error",
        severity="error",
        event_time=utc_now().replace(tzinfo=None),
        country="DE",
        ip="198.51.100.5",
    )
    store_event(
        db_session,
        source="test",
        plugin="test",
        event_type="access.error",
        severity="error",
        event_time=utc_now().replace(tzinfo=None),
        country="RU",
        ip="198.51.100.8",
    )
    store_event(
        db_session,
        source="test",
        plugin="core",
        event_type="system.plugin_error",
        severity="error",
        event_time=utc_now().replace(tzinfo=None),
    )

    assert len(_notifications(db_session, "test.error_ru")) == 1
    assert len(_notifications(db_session, "test.system_wildcard")) == 1


def test_high_insight_queues_scanner_notification_but_medium_does_not(db_session):
    high = Insight(type="scanner_detected", level="high", title="Scanner detected", timestamp=utc_now().replace(tzinfo=None))
    medium = Insight(type="scanner_detected", level="medium", title="Scanner detected", timestamp=utc_now().replace(tzinfo=None))
    db_session.add_all([high, medium])
    db_session.flush()

    handle_insight(db_session, high)
    handle_insight(db_session, medium)

    notifications = _notifications(db_session, "core.scanner_detected")
    assert len(notifications) == 1
    assert notifications[0].payload is not None
    assert notifications[0].payload["insight_id"] == high.id


def test_duplicate_event_does_not_queue_a_second_notification(db_session):
    values = {
        "source": "test",
        "plugin": "crowdsec",
        "event_type": "security.ban",
        "severity": "warning",
        "event_time": utc_now().replace(tzinfo=None),
        "ip": "198.51.100.6",
    }
    store_event(db_session, **values)
    store_event(db_session, **values)

    assert len(_notifications(db_session, "core.crowdsec_ban")) == 1


def test_disabled_rule_does_not_queue_notification(db_session):
    rule = db_session.query(NotificationRule).filter_by(rule_id="core.crowdsec_ban").one()
    rule.enabled = False
    db_session.commit()
    invalidate_rules_cache()

    store_event(
        db_session,
        source="test",
        plugin="crowdsec",
        event_type="security.ban",
        severity="warning",
        event_time=utc_now().replace(tzinfo=None),
        ip="198.51.100.7",
    )

    assert _notifications(db_session, "core.crowdsec_ban") == []


def test_plugin_error_event_is_only_created_on_transition_to_error(db_session):
    manager = get_plugin_manager()

    manager._update_diagnostic(db_session, "test_plugin", "healthy", None)
    db_session.flush()
    manager._update_diagnostic(db_session, "test_plugin", "error", "first failure")
    manager._update_diagnostic(db_session, "test_plugin", "error", "second failure")

    events = db_session.query(Event).filter(Event.event_type == "system.plugin_error").all()
    assert len(events) == 1
    assert events[0].data_json == {"plugin": "test_plugin", "message": "first failure"}


def test_pending_notifications_are_capped_for_single_count_rules(db_session):
    for offset in range(30):
        store_event(
            db_session,
            source="test",
            plugin="crowdsec",
            event_type="security.ban",
            severity="warning",
            event_time=utc_now().replace(tzinfo=None),
            ip=f"198.51.100.{offset + 10}",
        )

    assert len(_notifications(db_session, "core.crowdsec_ban")) == 25
