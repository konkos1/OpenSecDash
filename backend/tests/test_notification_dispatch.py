from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
import ssl
from threading import Barrier
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.time import utc_now
from app.database.base import Base
from app.models.core import Notification, NotificationRule
from app.models.events import Event
from app.models.systems import System
from app.services import notifications
from app.services.events import store_event
from app.services.notification_channels import EmailChannel
from app.services.notifications import dispatch_pending_notifications, invalidate_rules_cache, seed_default_notification_rules
from app.services.settings import save_setting


class FakeChannel:
    id = "email"

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.messages: list[tuple[str, str, str | None]] = []

    def is_configured(self, db) -> bool:
        return True

    def send(self, db, subject: str, body: str, html_body: str | None = None) -> None:
        if self.fail:
            raise RuntimeError("SMTP unavailable")
        self.messages.append((subject, body, html_body))


@pytest.fixture(autouse=True)
def notification_settings(db_session, _test_secret_key):
    seed_default_notification_rules(db_session)
    for key, value in {
        "notifications.enabled": "true",
        "notifications.base_url": "http://dashboard.example",
        "notifications.smtp_host": "smtp.example",
        "notifications.smtp_sender": "sender@example",
        "notifications.smtp_recipient": "admin@example",
    }.items():
        save_setting(db_session, key, value)
    db_session.commit()
    invalidate_rules_cache()


def _pending(rule_id: str, **payload) -> Notification:
    return Notification(rule_id=rule_id, channel="email", status="pending", payload=payload)


def test_dispatch_sends_pending_event_with_deep_link(db_session, monkeypatch):
    channel = FakeChannel()
    monkeypatch.setattr(notifications, "get_channel", lambda _: channel)
    save_setting(db_session, "domain", "homelab.example")
    item = _pending("core.crowdsec_ban", source="event", type="security.ban", ip="1.2.3.4", country="RU", path="/wp-login.php", severity="warning")
    db_session.add(item)
    db_session.commit()

    assert dispatch_pending_notifications(db_session) == 1
    assert item.status == "sent"
    assert item.subject == "[OpenSecDash] homelab.example · CrowdSec ban"
    assert item.sent_at is not None
    assert "IP: 1.2.3.4" in channel.messages[0][1]
    assert "http://dashboard.example/ip/1.2.3.4" in channel.messages[0][1]
    assert '<a href="http://dashboard.example/ip/1.2.3.4"' in (channel.messages[0][2] or "")
    assert "cid:opensecdash-logo" in (channel.messages[0][2] or "")


def test_dispatch_omits_links_without_base_url(db_session, monkeypatch):
    channel = FakeChannel()
    monkeypatch.setattr(notifications, "get_channel", lambda _: channel)
    save_setting(db_session, "notifications.base_url", "")
    db_session.add(_pending("core.crowdsec_ban", source="event", type="security.ban", ip="1.2.3.4"))
    db_session.commit()

    dispatch_pending_notifications(db_session)
    assert "Show events" not in channel.messages[0][1]


def test_dispatch_aggregates_pending_notifications_and_respects_cooldown(db_session, monkeypatch):
    channel = FakeChannel()
    monkeypatch.setattr(notifications, "get_channel", lambda _: channel)
    db_session.add_all([_pending("core.crowdsec_ban", source="event", type="security.ban", ip=f"198.51.100.{index}") for index in range(6)])
    db_session.commit()

    dispatch_pending_notifications(db_session)
    assert len(channel.messages) == 1
    assert "6 × CrowdSec ban" in channel.messages[0][1]
    assert "… and 1 more" in channel.messages[0][1]
    db_session.add(_pending("core.crowdsec_ban", source="event", type="security.ban", ip="198.51.100.99"))
    db_session.commit()
    assert dispatch_pending_notifications(db_session) == 0
    for sent in db_session.query(Notification).filter_by(rule_id="core.crowdsec_ban", status="sent").all():
        sent.sent_at = utc_now().replace(tzinfo=None) - timedelta(minutes=2)
    db_session.commit()
    dispatch_pending_notifications(db_session)
    assert len(channel.messages) == 2


def test_threshold_skips_expired_pending_and_sends_when_reached(db_session, monkeypatch):
    channel = FakeChannel()
    monkeypatch.setattr(notifications, "get_channel", lambda _: channel)
    rule = NotificationRule(rule_id="test.threshold", name="Threshold", source="event", match_types=["test"], min_count=3, window_minutes=10)
    old = _pending("test.threshold", source="event", type="test")
    old.created_at = utc_now().replace(tzinfo=None) - timedelta(minutes=11)
    db_session.add_all([rule, old, _pending("test.threshold", source="event", type="test"), _pending("test.threshold", source="event", type="test")])
    db_session.commit()
    dispatch_pending_notifications(db_session)
    assert old.status == "skipped"
    assert channel.messages == []
    db_session.add(_pending("test.threshold", source="event", type="test"))
    db_session.commit()
    dispatch_pending_notifications(db_session)
    assert len(channel.messages) == 1


def test_dispatch_marks_failed_and_continues_with_other_rules(db_session, monkeypatch):
    failed_channel = FakeChannel(fail=True)
    successful_channel = FakeChannel()
    monkeypatch.setattr(notifications, "get_channel", lambda channel_id: failed_channel if channel_id == "email" else successful_channel)
    failing = _pending("core.crowdsec_ban", source="event", type="security.ban")
    other_rule = NotificationRule(rule_id="test.other", name="Other", source="event", match_types=["other"], channel="other")
    successful = _pending("test.other", source="event", type="other")
    db_session.add_all([failing, other_rule, successful])
    db_session.commit()

    dispatch_pending_notifications(db_session)
    assert failing.status == "failed"
    assert failing.error is not None
    assert "SMTP unavailable" in failing.error
    assert successful.status == "sent"


def test_unconfigured_channel_keeps_pending_and_offline_state_changes_once(db_session, monkeypatch):
    monkeypatch.setattr(notifications, "get_channel", lambda _: None)
    pending = _pending("core.crowdsec_ban", source="event", type="security.ban")
    system = System(vmid="test-1", hostname="offline.example", source_plugin="proxmox_assets", last_seen=utc_now().replace(tzinfo=None) - timedelta(days=2))
    db_session.add_all([pending, system])
    db_session.commit()

    dispatch_pending_notifications(db_session)
    db_session.expire_all()
    assert system.offline_event_for_last_seen == system.last_seen
    dispatch_pending_notifications(db_session)
    assert pending.status == "pending"
    assert db_session.query(Event).filter_by(event_type="system.asset_offline").count() == 1


def test_offline_detection_tracks_each_last_seen_cycle(db_session, monkeypatch):
    monkeypatch.setattr(notifications, "get_channel", lambda _: None)
    now = utc_now().replace(tzinfo=None)
    system = System(
        vmid="test-cycle",
        hostname="cycle.example",
        source_plugin="proxmox_assets",
        last_seen=now - timedelta(days=2),
    )
    db_session.add(system)
    db_session.commit()

    dispatch_pending_notifications(db_session)
    system.last_seen = now
    db_session.commit()
    dispatch_pending_notifications(db_session)
    system.last_seen = now - timedelta(days=3)
    db_session.commit()
    dispatch_pending_notifications(db_session)

    events = db_session.query(Event).filter_by(event_type="system.asset_offline").all()
    assert len(events) == 2


def test_offline_detection_uses_source_specific_cutoffs(db_session, monkeypatch):
    monkeypatch.setattr(notifications, "get_channel", lambda _: None)
    last_seen = utc_now().replace(tzinfo=None) - timedelta(days=2)
    db_session.add_all(
        [
            System(
                vmid="test-proxmox-cutoff",
                hostname="proxmox.example",
                source_plugin="proxmox_assets",
                last_seen=last_seen,
            ),
            System(
                vmid="test-default-cutoff",
                hostname="default.example",
                source_plugin="json_assets",
                last_seen=last_seen,
            ),
        ]
    )
    db_session.commit()

    dispatch_pending_notifications(db_session)

    events = db_session.query(Event).filter_by(event_type="system.asset_offline").all()
    assert [event.hostname for event in events] == ["proxmox.example"]


def test_offline_transition_claim_has_one_winner_across_workers(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'offline-claim.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    setup_session = session_factory()
    last_seen = utc_now().replace(tzinfo=None) - timedelta(days=2)
    system = System(
        vmid="test-worker-race",
        hostname="worker-race.example",
        source_plugin="proxmox_assets",
        last_seen=last_seen,
    )
    setup_session.add(system)
    setup_session.commit()
    system_id = system.id
    setup_session.close()
    ready = Barrier(2)

    def claim() -> bool:
        db = session_factory()
        try:
            observed_last_seen = db.query(System.last_seen).filter(System.id == system_id).scalar()
            assert observed_last_seen is not None
            ready.wait()
            claimed = notifications._claim_offline_system(db, system_id, observed_last_seen)
            db.commit()
            return claimed
        finally:
            db.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: claim(), range(2)))
        assert sorted(results) == [False, True]
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_email_channel_uses_starttls_ssl_and_plain_smtp(db_session, monkeypatch):
    smtp = MagicMock()
    smtp_instance = MagicMock()
    smtp.return_value.__enter__.return_value = smtp_instance
    smtp_ssl = MagicMock()
    smtp_ssl.return_value.__enter__.return_value = MagicMock()
    monkeypatch.setattr("app.services.notification_channels.smtplib.SMTP", smtp)
    monkeypatch.setattr("app.services.notification_channels.smtplib.SMTP_SSL", smtp_ssl)
    channel = EmailChannel()
    save_setting(db_session, "notifications.smtp_sender", "OpenSecDash <sender@example>")
    for security in ("starttls", "none", "ssl"):
        save_setting(db_session, "notifications.smtp_security", security)
        db_session.commit()
        channel.send(db_session, "Subject", "Body")
    assert smtp.call_count == 2
    starttls_context = smtp_instance.starttls.call_args.kwargs["context"]
    ssl_context = smtp_ssl.call_args.kwargs["context"]
    assert starttls_context.verify_mode == ssl.CERT_REQUIRED
    assert starttls_context.check_hostname is True
    assert ssl_context.verify_mode == ssl.CERT_REQUIRED
    assert ssl_context.check_hostname is True
    smtp_ssl.assert_called_once()
    message = smtp_instance.send_message.call_args_list[0].args[0]
    assert message["From"] == "OpenSecDash <sender@example>"
    assert message.get_body(preferencelist=("plain",)).get_content().strip() == "Body"
    assert "<title>Subject</title>" in message.get_body(preferencelist=("html",)).get_content()
    logo_parts = [part for part in message.walk() if part.get_content_type() == "image/png"]
    assert len(logo_parts) == 1
    assert logo_parts[0]["Content-ID"] == "<opensecdash-logo>"


def test_email_channel_adds_custom_ca_to_default_trust_store(db_session, monkeypatch):
    context = MagicMock()
    smtp = MagicMock()
    monkeypatch.setattr("app.services.notification_channels.ssl.create_default_context", MagicMock(return_value=context))
    monkeypatch.setattr("app.services.notification_channels.smtplib.SMTP", smtp)
    save_setting(db_session, "notifications.smtp_ca_file", "/certs/homelab-ca.pem")
    db_session.commit()

    EmailChannel().send(db_session, "Subject", "Body")

    context.load_verify_locations.assert_called_once_with(cafile="/certs/homelab-ca.pem")
    smtp.return_value.__enter__.return_value.starttls.assert_called_once_with(context=context)
