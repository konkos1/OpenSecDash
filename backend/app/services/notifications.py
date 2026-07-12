import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.core.time import utc_now
from app.models.core import Insight, Notification, NotificationRule
from app.models.events import Event


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
    global _rules_cache, _rules_loaded_at, _notifications_enabled_cache
    _rules_cache = None
    _rules_loaded_at = None
    _notifications_enabled_cache = None


def _active_rules(db: Session) -> tuple[list[NotificationRuleSnapshot], bool]:
    global _rules_cache, _rules_loaded_at, _notifications_enabled_cache
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
        _rules_loaded_at = now
    return _rules_cache, _notifications_enabled_cache is True


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
