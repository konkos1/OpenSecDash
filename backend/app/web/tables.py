from __future__ import annotations

import logging
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.logging import redacted_setting_value
from app.core.secrets import decrypt_setting_value, encrypt_setting_value
from app.core.template_context import get_setting_value
from app.core.time import local_day_start_as_utc, resolve_timezone, utc_now
from app.models.assets import Asset
from app.models.events import Event
from app.models.settings import Setting
from app.services.asset_hosts import event_matches_asset_host, find_asset_by_host
from app.web.redirects import safe_local_redirect_target

logger = logging.getLogger(__name__)

TABLE_COLUMN_DEFINITIONS = [
    {"key": "time", "label_key": "common.time"},
    {"key": "type", "label_key": "events.type"},
    {"key": "severity", "label_key": "events.severity"},
    {"key": "ip", "label_key": "events.ip"},
    {"key": "country", "label_key": "events.country"},
    {"key": "city", "label_key": "events.city"},
    {"key": "status", "label_key": "events.status"},
    {"key": "path", "label_key": "common.path"},
    {"key": "url", "label_key": "common.url"},
    {"key": "host", "label_key": "access.host"},
    {"key": "method", "label_key": "access.method"},
    {"key": "user_agent", "label_key": "events.user_agent"},
    {"key": "router", "label_key": "events.router"},
    {"key": "service", "label_key": "events.service"},
    {"key": "asn", "label_key": "events.asn"},
    {"key": "isp", "label_key": "events.isp"},
]
TABLE_COLUMN_KEYS = [str(item["key"]) for item in TABLE_COLUMN_DEFINITIONS]
DEFAULT_EVENTS_COLUMNS = "time,type,severity,ip,country,status,url"
DEFAULT_ACCESS_COLUMNS = "time,ip,host,method,status,path"
TIME_RANGE_PRESETS = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}
TIME_RANGE_VALUES = {*TIME_RANGE_PRESETS, "custom"}


def save_setting(db: Session, key: str, value: str) -> None:
    # Sensitive values (passwords, tokens, ...) are encrypted at rest; the
    # comparison below runs on the decrypted value so re-saving an unchanged
    # secret doesn't produce a new ciphertext (Fernet output is randomized)
    # and a misleading "Setting changed" log line on every settings save.
    stored_value = encrypt_setting_value(key, value)
    setting = db.query(Setting).filter(Setting.key == key).first()
    if setting is None:
        db.add(Setting(key=key, value=stored_value))
        logger.info("Setting created key=%s value=%s", key, redacted_setting_value(key, value))
    elif decrypt_setting_value(key, setting.value) != value:
        old_value = decrypt_setting_value(key, setting.value)
        setting.value = stored_value
        logger.info(
            "Setting changed key=%s old=%s new=%s",
            key,
            redacted_setting_value(key, old_value),
            redacted_setting_value(key, value),
        )
    else:
        logger.debug("Setting unchanged key=%s", key)


def today_start(db: Session) -> datetime:
    return local_day_start_as_utc(get_setting_value(db, "timezone", "auto"))


def today_hour_range(db: Session, hour: int) -> tuple[datetime, datetime]:
    timezone = resolve_timezone(get_setting_value(db, "timezone", "auto"))
    local_now = utc_now().astimezone(timezone)
    local_start = local_now.replace(hour=hour, minute=0, second=0, microsecond=0)
    local_end = local_start + timedelta(hours=1)
    return (
        local_start.astimezone(ZoneInfo("UTC")).replace(tzinfo=None),
        local_end.astimezone(ZoneInfo("UTC")).replace(tzinfo=None),
    )


def parse_snapshot_before(value: str | None) -> datetime | None:
    """Parse the snapshot cutoff carried through Events/Access filter forms."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def clean_time_range(value: str | None) -> str | None:
    text = (value or "").strip()
    return text if text in TIME_RANGE_VALUES else None


def time_range_start(value: str | None, custom_from: str | None = None) -> datetime | None:
    """Return the UTC start for a selected preset or a custom URL range."""
    if value == "custom":
        return parse_snapshot_before(custom_from)
    offset = TIME_RANGE_PRESETS.get(value or "")
    if offset is None:
        return None
    return (utc_now() - offset).replace(tzinfo=None)


def table_columns(db: Session, setting_key: str, default: str) -> tuple[list[dict[str, object]], set[str]]:
    configured = get_setting_value(db, setting_key, default)
    active = {key for key in configured.split(",") if key in TABLE_COLUMN_KEYS}
    if not active:
        active = {key for key in default.split(",") if key in TABLE_COLUMN_KEYS}
    columns = [{**definition, "active": definition["key"] in active} for definition in TABLE_COLUMN_DEFINITIONS]
    return columns, active


def save_table_columns(db: Session, setting_key: str, selected: list[str], default: str) -> None:
    values = [key for key in TABLE_COLUMN_KEYS if key in set(selected)]
    save_setting(db, setting_key, ",".join(values) if values else default)


def column_redirect_url(request: Request, fallback: str, snapshot_before: str | None) -> str:
    target = safe_local_redirect_target(request, request.headers.get("referer"), fallback)
    if not snapshot_before:
        return target
    parts = urlsplit(target)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["snapshot_before"] = snapshot_before
    return urlunsplit(("", "", parts.path, urlencode(query), parts.fragment))


def asset_links_for_events(db: Session, events: list[Event]) -> dict[int, str]:
    # One batched asset lookup for the whole page instead of up to two
    # queries per rendered event row; the host fallback goes through
    # find_asset_by_host's session-level host map cache.
    asset_ids = {event.asset_id for event in events if event.asset_id}
    assets_by_id = {asset.id: asset for asset in db.query(Asset).filter(Asset.id.in_(asset_ids)).all()} if asset_ids else {}
    links = {}
    for event in events:
        asset = assets_by_id.get(event.asset_id) if event.asset_id else None
        if asset is not None and not event_matches_asset_host(event, asset):
            asset = None
        if asset is None:
            asset = find_asset_by_host(db, event.hostname)
        if asset is not None:
            links[event.id] = f"/assets/app/{asset.id}"
    return links


def clean_filter_value(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def clean_url_value(value: str) -> str:
    """Remove accidental whitespace from URL-only inputs.

    Do not use this for fields that may contain file paths; POSIX/Windows paths
    can legitimately include spaces.
    """
    return "".join(str(value).split())


def utc_search_terms_for_ui_time(q: str | None, timezone_name: str) -> list[str]:
    if not q:
        return []
    text = q.strip()
    try:
        timezone = ZoneInfo(timezone_name) if timezone_name and timezone_name != "auto" else None
    except ZoneInfoNotFoundError:
        timezone = None
    if timezone is None:
        return []

    formats = (
        ("%Y-%m-%d %H:%M:%S", "datetime_seconds"),
        ("%Y-%m-%d %H:%M", "datetime_minutes"),
        ("%Y-%m-%d", "date"),
        ("%H:%M:%S", "time_seconds"),
        ("%H:%M", "time_minutes"),
    )
    terms: list[str] = []
    for fmt, kind in formats:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if kind.startswith("time"):
            today = utc_now().astimezone(timezone)
            parsed = parsed.replace(year=today.year, month=today.month, day=today.day)
        utc_value = parsed.replace(tzinfo=timezone).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        if kind == "datetime_seconds":
            terms.append(utc_value.strftime("%Y-%m-%d %H:%M:%S"))
        elif kind == "datetime_minutes":
            terms.append(utc_value.strftime("%Y-%m-%d %H:%M"))
        elif kind == "date":
            terms.append(utc_value.strftime("%Y-%m-%d"))
        elif kind == "time_seconds":
            terms.append(utc_value.strftime("%H:%M:%S"))
        elif kind == "time_minutes":
            terms.append(utc_value.strftime("%H:%M"))
    return list(dict.fromkeys(terms))
