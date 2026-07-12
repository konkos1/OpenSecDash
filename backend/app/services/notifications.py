import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.core.time import utc_now
from app.core.i18n import translate
from app.models.core import Insight, Notification, NotificationRule
from app.models.events import Event
from app.models.systems import System
from app.services.asset_hosts import asset_last_seen_stale
from app.services.notification_channels import get_channel


logger = logging.getLogger(__name__)

RULE_CACHE_TTL_SECONDS = 60
BACKLOG_PROTECTION_WINDOW = timedelta(minutes=15)
PENDING_NOTIFICATION_LIMIT = 25


@dataclass(frozen=True)
class NotificationRuleSnapshot:
    rule_id: str
    source: str
    match_types: tuple[str, ...]
    min_severity: str
    countries: tuple[str, ...]
    asset_id: int | None
    channel: str
    min_count: int
    window_minutes: int


_rules_cache: list[NotificationRuleSnapshot] | None = None
_rules_loaded_at: float | None = None
_notifications_enabled_cache: bool | None = None
_smtp_configured_cache: bool | None = None
_last_offline_state: dict[int, bool] = {}

EVENT_SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2, "critical": 3}
INSIGHT_LEVEL_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


DEFAULT_NOTIFICATION_RULES = (
    {
        "rule_id": "core.crowdsec_ban",
        "name": "CrowdSec ban",
        "source": "event",
        "match_types": ["security.ban"],
        "min_severity": "warning",
        "min_count": 1,
        "window_minutes": 10,
        "cooldown_minutes": 1,
    },
    {
        "rule_id": "core.scanner_detected",
        "name": "Scanner detected",
        "source": "insight",
        "match_types": ["*"],
        "min_severity": "high",
        "min_count": 1,
        "window_minutes": 10,
        "cooldown_minutes": 5,
    },
    {
        "rule_id": "core.asset_offline",
        "name": "Asset offline",
        "source": "event",
        "match_types": ["system.asset_offline"],
        "min_severity": "warning",
        "min_count": 1,
        "window_minutes": 10,
        "cooldown_minutes": 60,
    },
    {
        "rule_id": "core.plugin_error",
        "name": "Plugin error",
        "source": "event",
        "match_types": ["system.plugin_error"],
        "min_severity": "error",
        "min_count": 1,
        "window_minutes": 10,
        "cooldown_minutes": 60,
    },
)


def seed_default_notification_rules(db: Session) -> None:
    """Add missing built-in notification rules without changing user settings."""
    existing_rule_ids = {rule_id for (rule_id,) in db.query(NotificationRule.rule_id).all()}
    for rule in DEFAULT_NOTIFICATION_RULES:
        if rule["rule_id"] not in existing_rule_ids:
            db.add(NotificationRule(**rule))


def invalidate_rules_cache() -> None:
    """Clear cached notification settings and rule snapshots."""
    global _rules_cache, _rules_loaded_at, _notifications_enabled_cache, _smtp_configured_cache
    _rules_cache = None
    _rules_loaded_at = None
    _notifications_enabled_cache = None
    _smtp_configured_cache = None


def _active_rules(db: Session) -> tuple[list[NotificationRuleSnapshot], bool]:
    global _rules_cache, _rules_loaded_at, _notifications_enabled_cache, _smtp_configured_cache
    now = time.monotonic()
    if _rules_cache is None or _rules_loaded_at is None or now - _rules_loaded_at >= RULE_CACHE_TTL_SECONDS:
        rows = db.query(NotificationRule).filter(NotificationRule.enabled == True).all()  # noqa: E712
        _rules_cache = [
            NotificationRuleSnapshot(
                rule_id=row.rule_id,
                source=row.source,
                match_types=tuple(row.match_types or ()),
                min_severity=row.min_severity,
                countries=tuple(row.countries or ()),
                asset_id=row.asset_id,
                channel=row.channel,
                min_count=row.min_count,
                window_minutes=row.window_minutes,
            )
            for row in rows
        ]
        _notifications_enabled_cache = get_setting_value(db, "notifications.enabled", "false").lower() == "true"
        _smtp_configured_cache = all(
            get_setting_value(db, key, "").strip()
            for key in ("notifications.smtp_host", "notifications.smtp_sender", "notifications.smtp_recipient")
        )
        _rules_loaded_at = now
    return _rules_cache, _notifications_enabled_cache is True and _smtp_configured_cache is True


def _type_matches(match_types: tuple[str, ...], value: str) -> bool:
    for match_type in match_types:
        if match_type == "*" or match_type == value:
            return True
        if match_type.endswith("*") and value.startswith(match_type[:-1]):
            return True
    return False


def _has_minimum_severity(value: str, minimum: str, order: dict[str, int]) -> bool:
    return order.get(value, 0) >= order.get(minimum, 0)


def _as_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.replace(tzinfo=None)


def _enqueue(db: Session, rule: NotificationRuleSnapshot, payload: dict[str, object]) -> None:
    now = utc_now().replace(tzinfo=None)
    window_start = now - timedelta(minutes=rule.window_minutes)
    pending = (
        db.query(Notification)
        .filter(
            Notification.rule_id == rule.rule_id,
            Notification.status == "pending",
            Notification.created_at >= window_start,
        )
        .count()
    )
    if rule.min_count == 1 and pending >= PENDING_NOTIFICATION_LIMIT:
        return
    db.add(Notification(rule_id=rule.rule_id, channel=rule.channel, status="pending", payload=payload))


def handle_event(db: Session, event: Event) -> None:
    """Queue matching notification records without interrupting event ingestion."""
    try:
        rules, notifications_enabled = _active_rules(db)
        if not notifications_enabled:
            return
        now = utc_now().replace(tzinfo=None)
        if event.event_time is None or _as_naive_utc(event.event_time) < now - BACKLOG_PROTECTION_WINDOW:
            return
        for rule in rules:
            if rule.source != "event" or not _type_matches(rule.match_types, event.event_type):
                continue
            if not _has_minimum_severity(event.severity, rule.min_severity, EVENT_SEVERITY_ORDER):
                continue
            if rule.countries and event.country not in rule.countries:
                continue
            if rule.asset_id is not None and event.asset_id != rule.asset_id:
                continue
            _enqueue(
                db,
                rule,
                {
                    "source": "event",
                    "event_id": event.id,
                    "type": event.event_type,
                    "severity": event.severity,
                    "ip": event.ip,
                    "country": event.country,
                    "path": event.path,
                    "asset_id": event.asset_id,
                },
            )
    except Exception:
        logger.exception("Notification engine failed while handling event id=%s", event.id)


def handle_insight(db: Session, insight: Insight) -> None:
    """Queue matching notification records without interrupting insight creation."""
    try:
        rules, notifications_enabled = _active_rules(db)
        if not notifications_enabled:
            return
        now = utc_now().replace(tzinfo=None)
        if insight.timestamp is None or _as_naive_utc(insight.timestamp) < now - BACKLOG_PROTECTION_WINDOW:
            return
        for rule in rules:
            if rule.source != "insight" or not _type_matches(rule.match_types, insight.type):
                continue
            if not _has_minimum_severity(insight.level, rule.min_severity, INSIGHT_LEVEL_ORDER):
                continue
            if rule.asset_id is not None and insight.asset_id != rule.asset_id:
                continue
            _enqueue(
                db,
                rule,
                {
                    "source": "insight",
                    "insight_id": insight.id,
                    "type": insight.type,
                    "level": insight.level,
                    "ip": insight.ip,
                    "title": insight.title,
                    "asset_id": insight.asset_id,
                },
            )
    except Exception:
        logger.exception("Notification engine failed while handling insight id=%s", insight.id)


def _notification_name(rule: NotificationRule) -> str:
    return rule.name


def render_notification(db: Session, rule: NotificationRule, pending: list[Notification]) -> tuple[str, str]:
    """Render one plain-text notification or digest in the configured language."""
    language = get_setting_value(db, "language", "en")
    text = lambda key: translate(key, language)
    name = _notification_name(rule)
    domain = get_setting_value(db, "domain", "").strip()
    instance_label = f" {domain}" if domain else ""
    subject = f"{text('notification.email.subject_prefix')}{instance_label} · {name}"
    base_url = get_setting_value(db, "notifications.base_url", "").rstrip("/")
    if len(pending) > 1:
        lines = [text("notification.email.digest_title").format(count=len(pending), name=name, minutes=rule.window_minutes), ""]
        for item in pending[:5]:
            payload = item.payload or {}
            parts = [str(payload[key]) for key in ("ip", "country", "path") if payload.get(key)]
            if parts:
                lines.append(" · ".join(parts))
        if len(pending) > 5:
            lines.extend(["", text("notification.email.and_more").format(count=len(pending) - 5)])
    else:
        payload = pending[0].payload or {}
        lines = [name]
        fields = (("ip", "notification.email.ip"), ("country", "notification.email.country"), ("path", "notification.email.path"), ("severity", "notification.email.severity"), ("level", "notification.email.level"))
        for key, label in fields:
            if payload.get(key):
                lines.append(f"{text(label)}: {payload[key]}")
    if base_url:
        payload = pending[0].payload or {}
        event_type = str(payload.get("type") or "")
        if event_type == "system.plugin_error":
            link_path, link_label = "/diagnostics", "notification.email.open_diagnostics"
        elif event_type == "system.asset_offline":
            link_path, link_label = "/assets", "notification.email.open_assets"
        elif payload.get("ip"):
            link_path, link_label = f"/ip/{payload['ip']}", "notification.email.open_ip"
        else:
            link_path, link_label = "/events", "notification.email.show_events"
        lines.extend(["", f"{text(link_label)}: {base_url}{link_path}"])
        if event_type not in {"system.plugin_error", "system.asset_offline"}:
            lines.append(f"{text('notification.email.show_events')}: {base_url}/events")
    return subject, "\n".join(lines)


def _detect_offline_systems(db: Session) -> None:
    now = utc_now().replace(tzinfo=None)
    for system in db.query(System).filter(System.last_seen.isnot(None)).all():
        if system.last_seen is None:
            continue
        offline = asset_last_seen_stale(system.last_seen, system.source_plugin, now)
        was_offline = _last_offline_state.get(system.id, False)
        _last_offline_state[system.id] = offline
        if not offline or was_offline:
            continue
        from app.services.events import store_event

        asset_id = system.assets[0].id if system.assets else None
        store_event(
            db,
            source="System",
            source_id="assets",
            plugin="core",
            event_type="system.asset_offline",
            severity="warning",
            asset_id=asset_id,
            hostname=system.hostname,
            data_json={"system": system.hostname, "last_seen": system.last_seen.isoformat()},
        )


def dispatch_pending_notifications(db: Session) -> int:
    """Detect offline systems and send eligible notification batches."""
    _detect_offline_systems(db)
    sent_count = 0
    now = utc_now().replace(tzinfo=None)
    if get_setting_value(db, "notifications.enabled", "false").lower() != "true":
        db.commit()
        return sent_count
    rule_ids = [rule_id for (rule_id,) in db.query(Notification.rule_id).filter(Notification.status == "pending").distinct().all()]
    for rule_id in rule_ids:
        rule = db.query(NotificationRule).filter(NotificationRule.rule_id == rule_id).first()
        if rule is None:
            continue
        channel = get_channel(rule.channel)
        if channel is None or not channel.is_configured(db):
            continue
        pending = db.query(Notification).filter(Notification.rule_id == rule_id, Notification.status == "pending").order_by(Notification.created_at.asc()).all()
        window_start = now - timedelta(minutes=rule.window_minutes)
        recent = [item for item in pending if item.created_at >= window_start]
        if rule.min_count > 1 and len(recent) < rule.min_count:
            for item in pending:
                if item.created_at < window_start:
                    item.status = "skipped"
            continue
        cooldown_start = now - timedelta(minutes=rule.cooldown_minutes)
        if db.query(Notification).filter(Notification.rule_id == rule_id, Notification.status == "sent", Notification.sent_at >= cooldown_start).first() is not None:
            continue
        if not pending:
            continue
        subject, body = render_notification(db, rule, pending)
        try:
            channel.send(db, subject, body)
        except Exception as exc:
            for item in pending:
                item.status = "failed"
                item.error = str(exc)[:2000]
            logger.exception("Notification delivery failed for rule %s", rule_id)
            continue
        sent_at = utc_now().replace(tzinfo=None)
        for item in pending:
            item.status = "sent"
            item.sent_at = sent_at
            item.subject = subject
        sent_count += len(pending)
    db.commit()
    return sent_count
