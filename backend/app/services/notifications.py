import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.core import plugin_registry
from app.core.i18n import translate
from app.core.logging import redact_sensitive
from app.core.template_context import get_setting_value
from app.core.time import format_datetime_for_timezone, resolve_timezone, utc_now
from app.models.assets import Asset
from app.models.core import Insight, Notification, NotificationRule
from app.models.events import Event
from app.models.systems import System
from app.services.asset_hosts import asset_stale_threshold
from app.services.insight_catalog import (
    available_insight_types,
    insight_availability,
    insight_definitions,
    insight_unavailable_reason,
    localized_insight_title,
)
from app.services.notification_channels import get_channel, render_email_html


logger = logging.getLogger(__name__)

RULE_CACHE_TTL_SECONDS = 60
BACKLOG_PROTECTION_WINDOW = timedelta(minutes=15)
PENDING_NOTIFICATION_LIMIT = 25
INSIGHT_NOTIFICATION_RULE_PREFIX = "insight."
LEGACY_INSIGHT_NOTIFICATION_RULE_ID = "core.scanner_detected"
EVENT_NOTIFICATION_DETAIL_KEYS = (
    "asn",
    "previous_provider_name",
    "provider_name",
    "provider_name_changed_at",
    "duration",
    "scenario",
    "plugin",
    "message",
    "last_seen",
    "vmid",
    "system_type",
    "source_plugin",
)
NOTIFICATION_DATETIME_DETAIL_KEYS = frozenset(
    {"occurred_at", "checked_at", "last_seen", "provider_name_changed_at"}
)
NOTIFICATION_DETAIL_FIELDS = (
    ("plugin", "notification.email.plugin"),
    ("asset_name", "notification.email.asset"),
    ("system_name", "notification.email.system"),
    ("vmid", "notification.email.vmid"),
    ("asset_type", "notification.email.asset_type"),
    ("system_type", "notification.email.system_type"),
    ("source_plugin", "notification.email.asset_source"),
    ("title", "notification.email.insight"),
    ("description", "notification.email.description"),
    ("occurred_at", "notification.email.occurred_at"),
    ("last_seen", "notification.email.last_seen"),
    ("checked_at", "notification.email.checked_at"),
    ("installed_version", "notification.email.installed_version"),
    ("latest_version", "notification.email.latest_version"),
    ("host_url", "notification.email.host_url"),
    ("ip", "notification.email.ip"),
    ("country", "notification.email.country"),
    ("path", "notification.email.path"),
    ("asn", "notification.email.asn"),
    ("previous_provider_name", "notification.email.previous_asn_organization"),
    ("provider_name", "notification.email.asn_organization"),
    ("provider_name_changed_at", "notification.email.detected_at"),
    ("duration", "notification.email.duration"),
    ("scenario", "notification.email.scenario"),
    ("severity", "notification.email.severity"),
    ("level", "notification.email.level"),
    ("confidence", "notification.email.confidence"),
    ("message", "notification.email.error"),
)


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


@dataclass(frozen=True)
class NotificationRuleView:
    rule: NotificationRule
    name: str
    available: bool
    unavailable_reason: str = ""


_rules_cache: list[NotificationRuleSnapshot] | None = None
_rules_loaded_at: float | None = None
_notifications_enabled_cache: bool | None = None
_smtp_configured_cache: bool | None = None

EVENT_SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2, "critical": 3}
INSIGHT_LEVEL_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


DEFAULT_NOTIFICATION_RULES = (
    {
        "rule_id": "core.crowdsec_ban",
        "name": "CrowdSec ban",
        "source": "event",
        "match_types": ["security.ban", "security.ban.asn_policy"],
        "min_severity": "warning",
        "min_count": 1,
        "window_minutes": 10,
        "cooldown_minutes": 1,
        "enabled": False,
    },
    {
        "rule_id": "core.asn_provider_changed",
        "name": "ASN organization changed",
        "source": "event",
        "match_types": ["security.asn_ban.provider_changed"],
        "min_severity": "warning",
        "min_count": 1,
        "window_minutes": 10,
        "cooldown_minutes": 60,
        "enabled": False,
    },
    {
        "rule_id": "core.asset_update_available",
        "name": "Asset update available",
        "source": "asset",
        "match_types": ["asset.update_available"],
        "min_severity": "info",
        "min_count": 1,
        "window_minutes": 10,
        "cooldown_minutes": 1,
        "enabled": False,
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
        "enabled": False,
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
        "enabled": False,
    },
)


def seed_default_notification_rules(db: Session) -> None:
    """Add missing built-in notification rules without changing user settings."""
    existing_rules = {rule.rule_id: rule for rule in db.query(NotificationRule).all()}
    for rule in DEFAULT_NOTIFICATION_RULES:
        if rule["rule_id"] not in existing_rules:
            db.add(NotificationRule(**rule))
    crowdsec_rule = existing_rules.get("core.crowdsec_ban")
    if crowdsec_rule is not None and crowdsec_rule.match_types == ["security.ban"]:
        crowdsec_rule.match_types = ["security.ban", "security.ban.asn_policy"]
        crowdsec_rule.updated_at = utc_now().replace(tzinfo=None)
    sync_insight_notification_rules(db)


def insight_notification_rule_id(insight_type: str) -> str:
    return f"{INSIGHT_NOTIFICATION_RULE_PREFIX}{insight_type}"


def _managed_insight_type(rule: NotificationRule) -> str | None:
    if not rule.rule_id.startswith(INSIGHT_NOTIFICATION_RULE_PREFIX):
        return None
    insight_type = rule.rule_id.removeprefix(INSIGHT_NOTIFICATION_RULE_PREFIX)
    if rule.source != "insight" or rule.match_types != [insight_type]:
        return None
    return insight_type


def sync_insight_notification_rules(db: Session) -> None:
    """Upsert one independently configurable notification rule per Insight type."""
    legacy = (
        db.query(NotificationRule)
        .filter(NotificationRule.rule_id == LEGACY_INSIGHT_NOTIFICATION_RULE_ID)
        .first()
    )
    legacy_enabled = legacy.enabled if legacy is not None else False
    if legacy is not None and legacy.enabled:
        legacy.enabled = False
        legacy.updated_at = utc_now().replace(tzinfo=None)

    now = utc_now().replace(tzinfo=None)
    for definition in insight_definitions(db).values():
        rule_id = insight_notification_rule_id(definition.type)
        existing = db.query(NotificationRule).filter(NotificationRule.rule_id == rule_id).first()
        if existing is None:
            db.add(
                NotificationRule(
                    rule_id=rule_id,
                    name=definition.title,
                    source="insight",
                    match_types=[definition.type],
                    min_severity="low",
                    min_count=1,
                    window_minutes=10,
                    cooldown_minutes=5,
                    enabled=(
                        legacy_enabled
                        and INSIGHT_LEVEL_ORDER.get(definition.level, 0) >= INSIGHT_LEVEL_ORDER["high"]
                    ),
                    created_at=now,
                    updated_at=now,
                )
            )
            continue
        if (
            existing.name != definition.title
            or existing.source != "insight"
            or existing.match_types != [definition.type]
            or existing.min_severity != "low"
        ):
            existing.name = definition.title
            existing.source = "insight"
            existing.match_types = [definition.type]
            existing.min_severity = "low"
            existing.updated_at = now
    db.flush()
    invalidate_rules_cache()


def notification_rule_views(db: Session, language: str) -> list[NotificationRuleView]:
    definitions = insight_definitions(db)
    views = []
    rules = (
        db.query(NotificationRule)
        .filter(NotificationRule.rule_id != LEGACY_INSIGHT_NOTIFICATION_RULE_ID)
        .order_by(NotificationRule.name)
        .all()
    )
    for rule in rules:
        insight_type = _managed_insight_type(rule)
        if insight_type is None:
            views.append(NotificationRuleView(rule=rule, name=rule.name, available=True))
            continue
        definition = definitions.get(insight_type)
        if definition is None:
            views.append(
                NotificationRuleView(
                    rule=rule,
                    name=rule.name,
                    available=False,
                    unavailable_reason=translate("notifications.insight_unavailable", language),
                )
            )
            continue
        availability = insight_availability(db, definition)
        views.append(
            NotificationRuleView(
                rule=rule,
                name=localized_insight_title(definition, language),
                available=availability.available,
                unavailable_reason=(
                    "" if availability.available else insight_unavailable_reason(db, definition, language)
                ),
            )
        )
    return views


def editable_notification_rule_ids(db: Session) -> set[str]:
    return {view.rule.rule_id for view in notification_rule_views(db, "en") if view.available}


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
        available_types = available_insight_types(db)
        snapshots = []
        for row in rows:
            insight_type = _managed_insight_type(row)
            if insight_type is not None and insight_type not in available_types:
                continue
            snapshots.append(
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
            )
        _rules_cache = snapshots
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


def notification_detail_rows(
    payload: dict[str, object],
    language: str,
    timezone_name: str,
) -> list[tuple[str, str]]:
    """Return localized, human-readable notification payload details."""
    rows = []
    for key, label in NOTIFICATION_DETAIL_FIELDS:
        value = payload.get(key)
        if value is None or value == "":
            continue
        value_text = str(value)
        if key in {"plugin", "source_plugin"}:
            plugin_name = plugin_registry.plugin_name(value_text)
            value_text = f"{plugin_name} ({value_text})" if plugin_name != value_text else value_text
        elif key in NOTIFICATION_DATETIME_DETAIL_KEYS:
            try:
                timestamp = datetime.fromisoformat(value_text.replace("Z", "+00:00"))
                display_timezone = resolve_timezone(timezone_name).key
                value_text = f"{format_datetime_for_timezone(timestamp, timezone_name)} ({display_timezone})"
            except ValueError:
                pass
        elif key == "confidence":
            try:
                value_text = f"{float(str(value)) * 100:.0f}%"
            except (TypeError, ValueError):
                pass
        rows.append((translate(label, language), value_text))
    return rows


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


def _enqueue_event(db: Session, rule: NotificationRuleSnapshot, payload: dict[str, object]) -> None:
    """Queue an event notification once and refresh its pending payload."""
    event_id = payload.get("event_id")
    # handle_event rejects events outside this same window, so older rows can
    # never represent a valid repeat of the event currently being handled.
    # Keeping the query inside the indexed rule/created-at window avoids
    # loading an unbounded notification history for every event.
    window_start = utc_now().replace(tzinfo=None) - BACKLOG_PROTECTION_WINDOW
    existing = (
        db.query(Notification)
        .filter(
            Notification.rule_id == rule.rule_id,
            Notification.created_at >= window_start,
        )
        .order_by(Notification.id.desc())
        .all()
    )
    for notification in existing:
        if (notification.payload or {}).get("event_id") != event_id:
            continue
        # Event enrichment may add fields after ingestion. Keep notifications
        # that have not been dispatched yet in sync without queueing a second
        # notification for the same event and rule.
        if notification.status == "pending" and notification.payload != payload:
            notification.payload = payload
        return
    _enqueue(db, rule, payload)


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
            event_data = event.data_json or {}
            details = {}
            for key in EVENT_NOTIFICATION_DETAIL_KEYS:
                value = event_data.get(key)
                if value is not None and value != "":
                    details[key] = redact_sensitive(value) if key == "message" else value
            _enqueue_event(
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
                    "system_name": event.hostname or event_data.get("system"),
                    "occurred_at": event.event_time.isoformat(),
                    **details,
                },
            )
    except Exception:
        logger.exception("Notification engine failed while handling event id=%s", event.id)


def handle_insight(db: Session, insight: Insight, occurred_at: datetime | None = None) -> None:
    """Queue matching notification records without interrupting insight creation."""
    try:
        if insight.id is None or insight.timestamp is None:
            db.flush([insight])
        rules, notifications_enabled = _active_rules(db)
        if not notifications_enabled:
            return
        now = utc_now().replace(tzinfo=None)
        notification_time = occurred_at or insight.timestamp
        if notification_time is None or _as_naive_utc(notification_time) < now - BACKLOG_PROTECTION_WINDOW:
            return
        for rule in rules:
            if rule.source != "insight" or not _type_matches(rule.match_types, insight.type):
                continue
            if not _has_minimum_severity(insight.level, rule.min_severity, INSIGHT_LEVEL_ORDER):
                continue
            if rule.asset_id is not None and insight.asset_id != rule.asset_id:
                continue
            asset = db.query(Asset).filter(Asset.id == insight.asset_id).first() if insight.asset_id is not None else None
            system = asset.system if asset is not None else None
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
                    "description": redact_sensitive(insight.description),
                    "confidence": insight.confidence,
                    "asset_id": insight.asset_id,
                    "asset_name": asset.name if asset is not None else None,
                    "system_name": system.hostname if system is not None else None,
                    "occurred_at": notification_time.isoformat(),
                },
            )
    except Exception:
        logger.exception("Notification engine failed while handling insight id=%s", insight.id)


def handle_asset_update(db: Session, asset: Asset, *, release_notes_url: str) -> None:
    """Queue a notification when a newly detected asset update matches a rule."""
    try:
        if asset.id is None:
            db.flush([asset])
        rules, notifications_enabled = _active_rules(db)
        if not notifications_enabled:
            return
        system = asset.system
        for rule in rules:
            if rule.source != "asset" or not _type_matches(rule.match_types, "asset.update_available"):
                continue
            if rule.asset_id is not None and asset.id != rule.asset_id:
                continue
            _enqueue(
                db,
                rule,
                {
                    "source": "asset",
                    "type": "asset.update_available",
                    "asset_id": asset.id,
                    "asset_name": asset.name,
                    "asset_type": asset.type,
                    "system_id": asset.system_id,
                    "system_name": system.hostname if system is not None else None,
                    "system_type": system.system_type if system is not None else None,
                    "vmid": system.vmid if system is not None else None,
                    "source_plugin": asset.source_plugin,
                    "installed_version": asset.version,
                    "latest_version": asset.latest_version,
                    "host_url": asset.host_url,
                    "checked_at": asset.last_checked.isoformat() if asset.last_checked is not None else None,
                    "release_notes_url": release_notes_url,
                },
            )
    except Exception:
        logger.exception("Notification engine failed while handling asset update id=%s", asset.id)


def render_notification(db: Session, rule: NotificationRule, pending: list[Notification]) -> tuple[str, str, str]:
    """Render one multipart notification or digest in the configured language."""
    language = get_setting_value(db, "language", "en")
    text = lambda key: translate(key, language)
    insight_type = _managed_insight_type(rule)
    definition = insight_definitions(db).get(insight_type) if insight_type is not None else None
    name = localized_insight_title(definition, language) if definition is not None else rule.name
    domain = get_setting_value(db, "domain", "").strip()
    instance_label = f" {domain}" if domain else ""
    base_url = get_setting_value(db, "notifications.base_url", "").rstrip("/")
    timezone_name = get_setting_value(db, "timezone", "auto")
    details: list[tuple[str, str]] = []
    items: list[str] = []
    more_text: str | None = None
    links: list[tuple[str, str]] = []
    subject = f"{text('notification.email.subject_prefix')}{instance_label} · {name}"

    if len(pending) > 1:
        heading = text("notification.email.digest_title").format(count=len(pending), name=name, minutes=rule.window_minutes)
        lines = [heading, ""]
        displayed = pending if rule.source == "asset" else pending[:5]
        for item in displayed:
            payload = item.payload or {}
            parts = [
                f"{label}: {value}"
                for label, value in notification_detail_rows(payload, language, timezone_name)
            ]
            if parts:
                summary = " · ".join(parts)
                lines.append(summary)
                items.append(summary)
        if rule.source != "asset" and len(pending) > 5:
            more_text = text("notification.email.and_more").format(count=len(pending) - 5)
            lines.extend(["", more_text])
    else:
        payload = pending[0].payload or {}
        heading = name
        lines = [name]
        details = notification_detail_rows(payload, language, timezone_name)
        lines.extend(f"{label}: {value}" for label, value in details)
    payload = pending[0].payload or {}
    if rule.source == "asset":
        for item in pending:
            item_payload = item.payload or {}
            release_notes_url = str(item_payload.get("release_notes_url") or "")
            if not release_notes_url:
                continue
            asset_name = str(item_payload.get("asset_name") or name)
            link = (
                text("notification.email.release_notes_for").format(asset=asset_name),
                release_notes_url,
            )
            if link not in links:
                links.append(link)
                lines.extend(["", f"{link[0]}: {link[1]}"])
        if base_url:
            asset_path = f"/assets/system/{payload['system_id']}" if payload.get("system_id") else "/assets"
            asset_link = (text("notification.email.open_assets"), f"{base_url}{asset_path}")
            links.append(asset_link)
            lines.extend(["", f"{asset_link[0]}: {asset_link[1]}"])
    elif base_url:
        event_type = str(payload.get("type") or "")
        if event_type == "system.plugin_error":
            link_path, link_label = "/diagnostics", "notification.email.open_diagnostics"
        elif event_type == "system.asset_offline":
            link_path, link_label = "/assets", "notification.email.open_assets"
        elif payload.get("ip"):
            link_path, link_label = f"/ip/{payload['ip']}", "notification.email.open_ip"
        else:
            link_path, link_label = "/events", "notification.email.show_events"
        primary_link = (text(link_label), f"{base_url}{link_path}")
        links.append(primary_link)
        lines.extend(["", f"{primary_link[0]}: {primary_link[1]}"])
        if event_type not in {"system.plugin_error", "system.asset_offline"} and link_path != "/events":
            events_link = (text("notification.email.show_events"), f"{base_url}/events")
            links.append(events_link)
            lines.append(f"{events_link[0]}: {events_link[1]}")
    plain_body = "\n".join(lines)
    html_body = render_email_html(
        subject=subject,
        heading=heading,
        language=language,
        details=details,
        items=items,
        more_text=more_text,
        links=links,
    )
    return subject, plain_body, html_body


def _detect_offline_systems(db: Session) -> None:
    now = utc_now().replace(tzinfo=None)
    proxmox_cutoff = now - asset_stale_threshold("proxmox_assets")
    default_cutoff = now - asset_stale_threshold(None)
    asset_id = (
        db.query(Asset.id)
        .filter(Asset.system_id == System.id)
        .order_by(Asset.id.asc())
        .limit(1)
        .scalar_subquery()
    )
    candidates = (
        db.query(
            System.id,
            System.hostname,
            System.last_seen,
            System.vmid,
            System.system_type,
            System.source_plugin,
            asset_id.label("asset_id"),
        )
        .filter(
            System.last_seen.isnot(None),
            System.offline_event_for_last_seen.is_distinct_from(System.last_seen),
            or_(
                and_(
                    System.source_plugin == "proxmox_assets",
                    System.last_seen < proxmox_cutoff,
                ),
                and_(
                    or_(
                        System.source_plugin.is_(None),
                        System.source_plugin != "proxmox_assets",
                    ),
                    System.last_seen < default_cutoff,
                ),
            ),
        )
        .all()
    )
    for system_id, hostname, last_seen, vmid, system_type, source_plugin, first_asset_id in candidates:
        if not _claim_offline_system(db, system_id, last_seen):
            continue
        from app.services.events import store_event

        store_event(
            db,
            source="System",
            source_id="assets",
            plugin="core",
            event_type="system.asset_offline",
            severity="warning",
            asset_id=first_asset_id,
            hostname=hostname,
            data_json={
                "system": hostname,
                "last_seen": last_seen.isoformat(),
                "vmid": vmid,
                "system_type": system_type,
                "source_plugin": source_plugin,
            },
        )


def _claim_offline_system(db: Session, system_id: int, last_seen: datetime) -> bool:
    """Claim one observed offline transition for the current transaction."""
    claimed = (
        db.query(System)
        .filter(
            System.id == system_id,
            System.last_seen == last_seen,
            System.offline_event_for_last_seen.is_distinct_from(last_seen),
        )
        .update(
            {System.offline_event_for_last_seen: last_seen},
            synchronize_session=False,
        )
    )
    return claimed == 1


def dispatch_pending_notifications(db: Session) -> int:
    """Detect offline systems and send eligible notification batches."""
    _detect_offline_systems(db)
    sent_count = 0
    now = utc_now().replace(tzinfo=None)
    if get_setting_value(db, "notifications.enabled", "false").lower() != "true":
        db.commit()
        return sent_count
    rule_ids = [rule_id for (rule_id,) in db.query(Notification.rule_id).filter(Notification.status == "pending").distinct().all()]
    available_types = available_insight_types(db)
    for rule_id in rule_ids:
        rule = db.query(NotificationRule).filter(NotificationRule.rule_id == rule_id).first()
        if rule is None:
            continue
        insight_type = _managed_insight_type(rule)
        if not rule.enabled or (insight_type is not None and insight_type not in available_types):
            db.query(Notification).filter(Notification.rule_id == rule_id, Notification.status == "pending").update(
                {Notification.status: "skipped"}, synchronize_session=False
            )
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
        subject, body, html_body = render_notification(db, rule, pending)
        try:
            channel.send(db, subject, body, html_body)
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
