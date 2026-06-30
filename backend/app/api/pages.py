from collections import Counter
from datetime import datetime, timedelta
from http import HTTPStatus
import io
import logging
from pathlib import Path
import platform
import zipfile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from markupsafe import Markup, escape
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.logging import configure_logging_from_db, redact_sensitive
from app.core.template_context import build_template_context, get_setting_value
from app.core.time import datetime_iso_utc, format_datetime_for_timezone, local_day_start_as_utc, resolve_timezone, utc_now
from app.database.dependencies import get_db
from app.models.assets import Asset
from app.models.core import Action, AggregationDaily, Datasource, Diagnostic, Insight, PluginRecord
from app.models.events import Event
from app.models.settings import Setting
from app.models.systems import System
from app.services.apps_inventory_updates import refresh_asset_update
from app.plugins.manager import get_plugin_manager
from app.services.asset_actions import (
    AssetActionAlreadyRunning,
    asset_action_running,
    import_assets_source_action,
    publish_asset_updates_action,
    refresh_asset_updates_action,
    run_asset_metadata_action,
)
from app.services.actions import ActionAlreadyRunning, create_action
from app.services.asset_hosts import event_matches_asset_host, find_asset_by_host, normalize_asset_host, sync_asset_host_events
from app.services.events import apply_event_filters, is_local_ip_value, store_event, tokenize_search_expression

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)


@pass_context
def format_duration(context, value: str | None) -> str:
    if not value:
        return "-"
    match = __import__("re").match(r"^(\d+)([smhdw])$", str(value).strip().lower())
    if not match:
        return str(value)
    amount = int(match.group(1))
    unit = match.group(2)
    language = str(context.get("language", "en"))
    labels = {
        "de": {"s": ("Sekunde", "Sekunden"), "m": ("Minute", "Minuten"), "h": ("Stunde", "Stunden"), "d": ("Tag", "Tage"), "w": ("Woche", "Wochen")},
        "en": {"s": ("second", "seconds"), "m": ("minute", "minutes"), "h": ("hour", "hours"), "d": ("day", "days"), "w": ("week", "weeks")},
    }
    singular, plural = labels.get(language, labels["en"])[unit]
    return f"{amount} {singular if amount == 1 else plural}"


@pass_context
def format_country_name(context, value: str | None) -> Markup | str:
    if not value:
        return "-"
    code = str(value).upper()
    return Markup('<span class="osd-country" data-country-code="{}">{}</span>'.format(escape(code), escape(code)))


@pass_context
def format_country_or_local(context, value: str | None, ip: str | None = None) -> Markup | str:
    if is_local_ip_value(ip):
        translator = context.get("t")
        return str(translator("common.local")) if callable(translator) else "local"
    return format_country_name(context, value)


@pass_context
def format_datetime(context, value: datetime | None) -> Markup | str:
    if value is None:
        return "-"
    timezone = str(context.get("timezone", "auto"))
    text = format_datetime_for_timezone(value, timezone)
    iso_utc = datetime_iso_utc(value)
    return Markup(
        '<span class="osd-datetime" data-datetime-utc="{}" data-timezone="{}">{}</span>'.format(
            escape(iso_utc),
            escape(timezone),
            escape(text),
        )
    )


def url_path_quote(value: str | None) -> str:
    return quote(str(value or ""), safe="")


def http_status_label(value: int | None) -> str:
    if value is None:
        return ""
    try:
        status = HTTPStatus(int(value))
        return f"{status.value} {status.phrase}"
    except ValueError:
        return str(value)


def event_url(event: Event) -> str:
    path = event.path or ""
    if not path:
        return ""
    if path.startswith(("http://", "https://")):
        return path

    data = event.data_json or {}
    for key in ("url", "full_url", "request_url", "absolute_url"):
        value = data.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value

    host = event.hostname or data.get("host") or data.get("request_host")
    if not host:
        return path

    scheme = data.get("scheme") or data.get("request_scheme") or data.get("RequestScheme") or data.get("proto") or data.get("protocol")
    if not scheme:
        router = str(data.get("router_name") or "").lower()
        if "https" in router or "websecure" in router:
            scheme = "https"
        elif "http" in router or "web" in router:
            scheme = "http"
    if not scheme:
        return f"{host}{path if path.startswith('/') else '/' + path}"

    scheme = str(scheme).replace("://", "").lower()
    if scheme not in {"http", "https"}:
        scheme = "https" if scheme.startswith("https") else "http"
    display_path = path if path.startswith("/") else f"/{path}"
    return f"{scheme}://{host}{display_path}"


templates.env.filters["datetime"] = format_datetime
templates.env.filters["duration"] = format_duration
templates.env.filters["country_name"] = format_country_name
templates.env.filters["country_or_local"] = format_country_or_local
templates.env.filters["url_path_quote"] = url_path_quote
templates.env.filters["event_url"] = event_url
templates.env.filters["http_status_label"] = http_status_label


def _redacted_setting_value(key: str, value: str | None) -> str:
    sensitive_parts = ("password", "token", "secret", "credential", "api_key", "apikey", "access_key")
    if any(part in key.lower() for part in sensitive_parts):
        return "<redacted>" if value else ""
    return redact_sensitive(str(value or ""))


def _debug_line(label: str, value: object = "") -> str:
    return f"{label}: {redact_sensitive(value)}"


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


def save_setting(db: Session, key: str, value: str) -> None:
    setting = db.query(Setting).filter(Setting.key == key).first()
    if setting is None:
        db.add(Setting(key=key, value=value))
        logger.info("Setting created key=%s value=%s", key, _redacted_setting_value(key, value))
    elif setting.value != value:
        old_value = setting.value
        setting.value = value
        logger.info(
            "Setting changed key=%s old=%s new=%s",
            key,
            _redacted_setting_value(key, old_value),
            _redacted_setting_value(key, value),
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


def render(request: Request, db: Session, template: str, **context):
    # All page routes go through this helper so global template context (i18n,
    # feature flags, settings) stays consistent and easy to exercise in tests.
    return templates.TemplateResponse(request=request, name=template, context={**build_template_context(db), "event_data_value": event_data_value, **context})


def is_plugin_enabled(db: Session, plugin_id: str) -> bool:
    return get_setting_value(db, f"plugin.{plugin_id}.enabled", "false") == "true"


COUNTRY_COORDINATES = {
    "AD": (42.5, 1.5), "AE": (24.0, 54.0), "AF": (33.0, 65.0), "AL": (41.0, 20.0), "AM": (40.0, 45.0),
    "AR": (-34.0, -64.0), "AT": (47.3, 13.3), "AU": (-25.0, 133.0), "AZ": (40.5, 47.5), "BA": (44.0, 18.0),
    "BD": (24.0, 90.0), "BE": (50.8, 4.5), "BG": (43.0, 25.0), "BR": (-10.0, -55.0), "BY": (53.0, 28.0),
    "CA": (56.0, -106.0), "CH": (47.0, 8.0), "CL": (-30.0, -71.0), "CN": (35.0, 103.0), "CO": (4.0, -72.0),
    "CZ": (49.8, 15.5), "DE": (51.0, 10.0), "DK": (56.0, 10.0), "EE": (59.0, 26.0), "EG": (27.0, 30.0),
    "ES": (40.0, -4.0), "FI": (64.0, 26.0), "FR": (46.0, 2.0), "GB": (54.0, -2.0), "GE": (42.0, 43.5),
    "GR": (39.0, 22.0), "HK": (22.3, 114.2), "HR": (45.0, 16.0), "HU": (47.0, 20.0), "ID": (-5.0, 120.0),
    "IE": (53.0, -8.0), "IL": (31.5, 35.0), "IN": (22.0, 79.0), "IR": (32.0, 53.0), "IT": (42.8, 12.8),
    "JP": (36.0, 138.0), "KR": (36.0, 128.0), "LT": (55.0, 24.0), "LU": (49.8, 6.1), "LV": (57.0, 25.0),
    "MA": (32.0, -5.0), "MD": (47.0, 29.0), "MX": (23.0, -102.0), "MY": (2.5, 112.5), "NG": (9.0, 8.0),
    "NL": (52.2, 5.3), "NO": (62.0, 10.0), "NZ": (-41.0, 174.0), "PH": (13.0, 122.0), "PK": (30.0, 70.0),
    "PL": (52.0, 20.0), "PT": (39.5, -8.0), "RO": (46.0, 25.0), "RS": (44.0, 21.0), "RU": (60.0, 90.0),
    "SA": (24.0, 45.0), "SE": (62.0, 15.0), "SG": (1.35, 103.8), "SI": (46.0, 15.0), "SK": (48.7, 19.5),
    "TH": (15.0, 101.0), "TR": (39.0, 35.0), "TW": (23.7, 121.0), "UA": (49.0, 32.0), "US": (39.8, -98.6),
    "VN": (16.0, 106.0), "ZA": (-29.0, 24.0),
}


def country_map_point(country: str, count: int, max_count: int) -> dict[str, object] | None:
    coordinates = COUNTRY_COORDINATES.get(str(country).upper())
    if coordinates is None:
        return None
    lat, lon = coordinates
    return {
        "country": country,
        "count": count,
        "x": ((lon + 180) / 360) * 100,
        "y": ((90 - lat) / 180) * 100,
        "radius": 3 + (0 if max_count == 0 else (count / max_count) * 9),
    }


def events_feature_enabled(db: Session) -> bool:
    return any(is_plugin_enabled(db, plugin_id) for plugin_id in ["crowdsec", "geoblock_log", "traefik_log"])


def require_plugin_enabled(db: Session, plugin_id: str) -> None:
    if not is_plugin_enabled(db, plugin_id):
        raise HTTPException(status_code=404, detail="Feature is disabled")


def require_events_feature_enabled(db: Session) -> None:
    if not events_feature_enabled(db):
        raise HTTPException(status_code=404, detail="Feature is disabled")


def latest_historical_rollup_day(db: Session, before_day: str) -> str | None:
    return db.query(func.max(AggregationDaily.date)).filter(AggregationDaily.date < before_day).scalar()


def dashboard_rollup_rows(db: Session, day: str | None, metric: str, limit: int = 5) -> list[dict[str, str | int]]:
    if not day:
        return []
    rows = (
        db.query(AggregationDaily.key, AggregationDaily.value)
        .filter(AggregationDaily.date == day, AggregationDaily.metric == metric)
        .order_by(AggregationDaily.value.desc(), AggregationDaily.key.asc())
        .limit(limit)
        .all()
    )
    return [{"key": str(key), "value": int(value or 0)} for key, value in rows]


@router.get("/")
def dashboard_page(request: Request, db: Session = Depends(get_db)):
    since = today_start(db)
    today_key = since.strftime("%Y-%m-%d")
    rollup_day = latest_historical_rollup_day(db, today_key)
    enabled_plugins = {
        "apps_inventory": is_plugin_enabled(db, "apps_inventory"),
        "crowdsec": is_plugin_enabled(db, "crowdsec"),
        "geoblock_log": is_plugin_enabled(db, "geoblock_log"),
        "traefik_log": is_plugin_enabled(db, "traefik_log"),
    }
    country_data_plugins = [
        plugin_id
        for plugin_id in ["crowdsec", "geoblock_log", "traefik_log"]
        if enabled_plugins[plugin_id]
    ]

    event_count = db.query(Event).filter(Event.event_time >= since, Event.plugin.in_(country_data_plugins)).count() if country_data_plugins else 0
    active_bans = db.query(Event).filter(Event.event_type.startswith("security.ban"), Event.event_time >= since, Event.plugin == "crowdsec").count() if enabled_plugins["crowdsec"] else 0
    geoblocks = db.query(Event).filter(Event.event_type == "security.geoblock", Event.event_time >= since, Event.plugin == "geoblock_log").count() if enabled_plugins["geoblock_log"] else 0
    access_events = db.query(Event).filter(Event.event_type.startswith("access."), Event.event_time >= since, Event.plugin == "traefik_log").count() if enabled_plugins["traefik_log"] else 0
    security_data_plugins = [
        plugin_id
        for plugin_id in ["crowdsec", "geoblock_log"]
        if enabled_plugins[plugin_id]
    ]
    top_countries = []
    attack_hours = []
    access_hours = []
    if country_data_plugins:
        top_countries = (
            db.query(Event.country, func.count(Event.id))
            .filter(
                Event.country.isnot(None),
                Event.event_time >= since,
                Event.plugin.in_(country_data_plugins),
            )
            .group_by(Event.country)
            .order_by(func.count(Event.id).desc())
            .limit(5)
            .all()
        )
    timezone_name = get_setting_value(db, "timezone", "auto")
    try:
        dashboard_timezone = ZoneInfo(timezone_name) if timezone_name and timezone_name != "auto" else ZoneInfo("UTC")
    except ZoneInfoNotFoundError:
        dashboard_timezone = ZoneInfo("UTC")

    def top_hours_for_plugins(plugin_ids: list[str], event_type: str) -> list[dict[str, object]]:
        if not plugin_ids:
            return []
        hour_counts: Counter[int] = Counter()
        for (event_time,) in (
            db.query(Event.event_time)
            .filter(Event.event_time >= since, Event.plugin.in_(plugin_ids))
            .all()
        ):
            if event_time is None:
                continue
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=ZoneInfo("UTC"))
            hour_counts[event_time.astimezone(dashboard_timezone).hour] += 1
        return [
            {"hour": hour, "count": count, "href": f"/events?event_type={event_type}&today=true&hour={hour:02d}"}
            for hour, count in hour_counts.most_common(5)
        ]

    attack_hours = top_hours_for_plugins(security_data_plugins, "security.*")
    access_hours = top_hours_for_plugins(["traefik_log"] if enabled_plugins["traefik_log"] else [], "access.*")
    latest_security_events = []
    if security_data_plugins:
        latest_security_events = (
            db.query(Event)
            .filter(Event.event_type.startswith("security."), Event.plugin.in_(security_data_plugins))
            .order_by(Event.event_time.desc())
            .limit(8)
            .all()
        )
    widgets = []
    if enabled_plugins["crowdsec"]:
        widgets.append({"title_key": "dashboard.active_bans", "value": active_bans, "href": "/events?event_type=security.ban*&today=true"})
    if enabled_plugins["geoblock_log"]:
        widgets.append({"title_key": "dashboard.geoblocks_today", "value": geoblocks, "href": "/events?event_type=security.geoblock&today=true"})
    if enabled_plugins["traefik_log"]:
        widgets.append({"title_key": "dashboard.access_today", "value": access_events, "href": "/events?event_type=access.*&today=true"})
    if enabled_plugins["apps_inventory"]:
        widgets.extend(
            [
                {"title_key": "dashboard.assets", "value": db.query(Asset).filter(Asset.is_active == True).count(), "href": "/assets"},
                {"title_key": "dashboard.updates", "value": db.query(Asset).filter(Asset.update_available == True).count(), "href": "/assets?updates=true"},
            ]
        )

    rollup_event_types = dashboard_rollup_rows(db, rollup_day, "event_type")
    rollup_scenarios = dashboard_rollup_rows(db, rollup_day, "scenario")
    rollup_total = sum(row["value"] for row in rollup_event_types if isinstance(row["value"], int))

    max_country_count = max((count for _, count in top_countries), default=0)
    country_heatmap = [
        point
        for country, count in top_countries
        if (point := country_map_point(country, count, max_country_count)) is not None
    ]

    return render(
        request,
        db,
        "dashboard.html",
        widgets=widgets,
        enabled_plugins=enabled_plugins,
        top_countries=top_countries,
        attack_hours=attack_hours,
        access_hours=access_hours,
        country_heatmap=country_heatmap,
        rollup_day=rollup_day or "-",
        rollup_event_types=rollup_event_types,
        rollup_scenarios=rollup_scenarios,
        rollup_total=rollup_total,
        rollup_plugins_enabled=bool(country_data_plugins),
        today_events_href="/events?today=true",
        country_data_plugins=country_data_plugins,
        latest_security_events=latest_security_events,
    )


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
    target = request.headers.get("referer") or fallback
    if not snapshot_before:
        return target
    parts = urlsplit(target)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["snapshot_before"] = snapshot_before
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def asset_links_for_events(db: Session, events: list[Event]) -> dict[int, str]:
    links = {}
    for event in events:
        asset = db.query(Asset).filter(Asset.id == event.asset_id).first() if event.asset_id else None
        if asset is not None and not event_matches_asset_host(event, asset):
            asset = None
        if asset is None:
            asset = find_asset_by_host(db, event.hostname)
        if asset is not None:
            links[event.id] = f"/assets/app/{asset.id}"
    return links


def event_data_value(event: Event, *keys: str) -> str | None:
    data = event.data_json or {}
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return str(value)
    return None


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


@router.get("/events")
def events_page(
    request: Request,
    event_type: str | None = None,
    ip: str | None = None,
    country: str | None = None,
    status_code: str | None = None,
    path: str | None = None,
    q: str | None = None,
    hide_local_ips: str | None = None,
    show_local_ips: str | None = None,
    today: str | None = None,
    hour: str | None = None,
    snapshot_before: str | None = None,
    db: Session = Depends(get_db),
):
    require_events_feature_enabled(db)
    status_code_value = clean_filter_value(status_code)
    country_value = clean_filter_value(country)
    if country_value and country_value != "-":
        country_value = country_value[:2].upper()
    q_value = clean_filter_value(q)
    timezone_name = get_setting_value(db, "timezone", "auto")
    enabled_event_plugins = [
        plugin_id
        for plugin_id in ["crowdsec", "geoblock_log", "traefik_log"]
        if is_plugin_enabled(db, plugin_id)
    ]
    q_tokens = [token for token in tokenize_search_expression(q_value or "") if token not in {"&&", "||", "(", ")"}]
    q_utc_terms_by_term = {token: utc_search_terms_for_ui_time(token, timezone_name) for token in q_tokens}
    today_enabled = today == "true"
    hour_value = int(hour) if hour and hour.isdigit() and 0 <= int(hour) <= 23 else None
    hour_start, hour_end = today_hour_range(db, hour_value) if hour_value is not None else (None, None)
    snapshot_cutoff = parse_snapshot_before(snapshot_before)
    event_time_to = min([value for value in [hour_end, snapshot_cutoff] if value is not None], default=None)
    filters = {
        "event_type": clean_filter_value(event_type),
        "ip": clean_filter_value(ip),
        "country": country_value,
        "status_code": int(status_code_value) if status_code_value and status_code_value.isdigit() else None,
        "path": clean_filter_value(path),
        "q": q_value,
        "q_utc_terms": utc_search_terms_for_ui_time(q_value, timezone_name),
        "q_utc_terms_by_term": q_utc_terms_by_term,
        "plugins": enabled_event_plugins,
        "hide_local_ips": hide_local_ips == "true",
        "show_local_ips": show_local_ips == "true",
        "event_time_from": hour_start or (today_start(db) if today_enabled else None),
        "event_time_to": event_time_to,
    }
    form_values = {
        "event_type": event_type or "",
        "ip": ip or "",
        "country": country or "",
        "status_code": status_code or "",
        "path": path or "",
        "q": q or "",
        "hide_local_ips": hide_local_ips == "true",
        "show_local_ips": show_local_ips == "true",
        "today": today_enabled,
        "hour": f"{hour_value:02d}" if hour_value is not None else "",
        "snapshot_before": snapshot_before or "",
    }
    events = apply_event_filters(db.query(Event), filters).order_by(Event.event_time.desc()).limit(200).all()
    column_options, active_columns = table_columns(db, "ui.events.visible_columns", DEFAULT_EVENTS_COLUMNS)
    event_asset_links = asset_links_for_events(db, events)
    return render(
        request,
        db,
        "events.html",
        events=events,
        filters=form_values,
        event_asset_links=event_asset_links,
        column_options=column_options,
        active_columns=active_columns,
        columns_setting_action="/events/columns",
        live_default=get_setting_value(db, "live_default", "true"),
    )


@router.post("/events/columns")
async def save_events_columns(request: Request, db: Session = Depends(get_db)):
    require_events_feature_enabled(db)
    form = await request.form()
    save_table_columns(db, "ui.events.visible_columns", [str(value) for value in form.getlist("columns")], DEFAULT_EVENTS_COLUMNS)
    db.commit()
    return RedirectResponse(url=column_redirect_url(request, "/events", str(form.get("snapshot_before") or "")), status_code=303)


@router.get("/access")
def access_page(
    request: Request,
    q: str | None = None,
    hide_local_ips: str | None = None,
    show_local_ips: str | None = None,
    today: str | None = None,
    snapshot_before: str | None = None,
    db: Session = Depends(get_db),
):
    require_plugin_enabled(db, "traefik_log")
    q_value = clean_filter_value(q)
    timezone_name = get_setting_value(db, "timezone", "auto")
    q_tokens = [token for token in tokenize_search_expression(q_value or "") if token not in {"&&", "||", "(", ")"}]
    q_utc_terms_by_term = {token: utc_search_terms_for_ui_time(token, timezone_name) for token in q_tokens}
    today_enabled = today == "true"
    snapshot_cutoff = parse_snapshot_before(snapshot_before)
    filters = {
        "event_type": "access.*",
        "q": q_value,
        "q_utc_terms": utc_search_terms_for_ui_time(q_value, timezone_name),
        "q_utc_terms_by_term": q_utc_terms_by_term,
        "plugins": ["traefik_log"],
        "hide_local_ips": hide_local_ips == "true",
        "show_local_ips": show_local_ips == "true",
        "event_time_from": today_start(db) if today_enabled else None,
        "event_time_to": snapshot_cutoff,
    }
    events = apply_event_filters(db.query(Event), filters).order_by(Event.event_time.desc()).limit(200).all()
    column_options, active_columns = table_columns(db, "ui.access.visible_columns", DEFAULT_ACCESS_COLUMNS)
    event_asset_links = asset_links_for_events(db, events)
    return render(
        request,
        db,
        "access.html",
        events=events,
        event_asset_links=event_asset_links,
        column_options=column_options,
        active_columns=active_columns,
        columns_setting_action="/access/columns",
        q=q or "",
        hide_local_ips=hide_local_ips == "true",
        show_local_ips=show_local_ips == "true",
        today=today_enabled,
        snapshot_before=snapshot_before or "",
        live_default=get_setting_value(db, "live_default", "true"),
    )


@router.post("/access/columns")
async def save_access_columns(request: Request, db: Session = Depends(get_db)):
    require_plugin_enabled(db, "traefik_log")
    form = await request.form()
    save_table_columns(db, "ui.access.visible_columns", [str(value) for value in form.getlist("columns")], DEFAULT_ACCESS_COLUMNS)
    db.commit()
    return RedirectResponse(url=column_redirect_url(request, "/access", str(form.get("snapshot_before") or "")), status_code=303)


@router.get("/crowdsec")
def crowdsec_page(request: Request, db: Session = Depends(get_db)):
    require_plugin_enabled(db, "crowdsec")
    bans = db.query(Event).filter(Event.event_type.startswith("security.ban")).order_by(Event.event_time.desc()).limit(100).all()
    scenario_counts: Counter[str] = Counter()
    scenario_rows = db.query(Event.data_json).filter(Event.event_type.startswith("security.ban")).all()
    for (data_json,) in scenario_rows:
        scenario = (data_json or {}).get("scenario") or ""
        scenario_counts[str(scenario or "")] += 1
    scenarios = [
        (scenario or None, count)
        for scenario, count in scenario_counts.most_common(10)
    ]
    countries = (
        db.query(Event.country, func.count(Event.id))
        .filter(Event.event_type.startswith("security.ban"), Event.country.isnot(None))
        .group_by(Event.country)
        .order_by(func.count(Event.id).desc())
        .limit(10)
        .all()
    )
    return render(request, db, "crowdsec.html", bans=bans, scenarios=scenarios, countries=countries)


@router.post("/actions/ip")
def action_ip_page(
    action_type: str = Form(...),
    ip: str = Form(...),
    duration: str = Form("4h"),
    confirmed: bool = Form(False),
    db: Session = Depends(get_db),
):
    try:
        create_action(db, action_type, ip, "ip", {"duration": duration, "reason": "Manual action"}, confirmed)
    except ActionAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        action = Action(
            timestamp=utc_now().replace(tzinfo=None),
            action_type=action_type,
            plugin_id="crowdsec" if action_type.startswith("security.") or action_type.startswith("crowdsec_") else "core",
            target_type="ip",
            target=ip,
            parameters={"duration": duration, "reason": "Manual action"},
            status="failed",
            result=str(exc),
            requires_confirmation=action_type in {"security.ban", "security.unban", "crowdsec_ban", "crowdsec_unban"},
        )
        db.add(action)
        db.flush()
        store_event(
            db,
            source="Action Framework",
            source_id="actions",
            plugin=action.plugin_id,
            plugin_id=action.plugin_id,
            event_type="action.failed",
            severity="error",
            ip=ip,
            data_json={"action_id": action.id, "action_type": action_type, "target": ip, "status": "failed", "result": str(exc), "manual": True, "trigger": "manual"},
        )
        db.commit()
    return RedirectResponse(url=f"/ip/{quote(ip, safe='')}", status_code=303)


@router.get("/ip/{ip:path}")
def ip_explorer_page(ip: str, request: Request, db: Session = Depends(get_db)):
    require_events_feature_enabled(db)
    events = db.query(Event).filter(Event.ip == ip).order_by(Event.event_time.desc()).limit(200).all()
    raw_insights = db.query(Insight).filter(Insight.ip == ip).order_by(Insight.timestamp.desc()).limit(100).all()
    insights = []
    seen_insight_types = set()
    for insight in raw_insights:
        if insight.type in seen_insight_types:
            continue
        seen_insight_types.add(insight.type)
        insights.append(insight)
        if len(insights) >= 50:
            break
    enabled_plugins = {
        "crowdsec": is_plugin_enabled(db, "crowdsec"),
        "geoblock_log": is_plugin_enabled(db, "geoblock_log"),
        "traefik_log": is_plugin_enabled(db, "traefik_log"),
    }
    count_widgets = []
    if enabled_plugins["crowdsec"]:
        count_widgets.append(
            {
                "key": "bans",
                "value": db.query(Event).filter(Event.ip == ip, Event.event_type.startswith("security.ban")).count(),
                "href": f"/events?ip={ip}&event_type=security.ban",
            }
        )
    if enabled_plugins["geoblock_log"]:
        count_widgets.append(
            {
                "key": "geoblocks",
                "value": db.query(Event).filter(Event.ip == ip, Event.event_type == "security.geoblock").count(),
                "href": f"/events?ip={ip}&event_type=security.geoblock",
            }
        )
    if enabled_plugins["traefik_log"]:
        count_widgets.append(
            {
                "key": "access",
                "value": db.query(Event).filter(Event.ip == ip, Event.event_type.startswith("access.")).count(),
                "href": f"/events?ip={ip}&event_type=access.*",
            }
        )
    return render(
        request,
        db,
        "ip.html",
        ip=ip,
        events=events,
        insights=insights,
        count_widgets=count_widgets,
        crowdsec_enabled=enabled_plugins["crowdsec"],
        local_ip_target=is_local_ip_value(ip),
    )


@router.get("/assets")
def assets_page(request: Request, show_inactive: bool = False, updates: bool = False, db: Session = Depends(get_db)):
    require_plugin_enabled(db, "apps_inventory")
    systems = db.query(System).order_by(System.hostname).all()
    system_rows = []
    for system in systems:
        apps_query = db.query(Asset).filter(Asset.system_id == system.id)
        if not show_inactive:
            apps_query = apps_query.filter(Asset.is_active == True)
        if updates:
            apps_query = apps_query.filter(Asset.update_available == True)
        app_count = apps_query.count()
        if updates and app_count == 0:
            continue
        update_available = (
            apps_query.filter(Asset.update_available == True).count() > 0
        )
        latest_asset = apps_query.order_by(Asset.last_seen.desc()).first()
        last_seen = latest_asset.last_seen if latest_asset is not None else system.last_seen
        system_rows.append(
            {
                "system": system,
                "app_count": app_count,
                "update_available": update_available,
                "last_seen": last_seen,
            }
        )
    mqtt_plugin_enabled = get_setting_value(db, "plugin.mqtt-hass.enabled", get_setting_value(db, "plugin.mqtt.enabled", "false")) == "true"
    mqtt_publishable_count = db.query(Asset).filter(Asset.mqtt_publish_enabled == True, Asset.version.isnot(None), Asset.latest_version.isnot(None), Asset.release_url.isnot(None)).count()
    return render(
        request,
        db,
        "assets.html",
        system_rows=system_rows,
        show_inactive=show_inactive,
        updates=updates,
        mqtt_plugin_enabled=mqtt_plugin_enabled,
        mqtt_publishable_count=mqtt_publishable_count,
        asset_action_busy=asset_action_running(),
        asset_import_running=asset_action_running("import"),
        asset_update_check_running=asset_action_running("refresh_updates"),
        asset_mqtt_publish_running=asset_action_running("mqtt_publish"),
    )


@router.post("/assets/mqtt-publish")
def assets_mqtt_publish_page(db: Session = Depends(get_db)):
    require_plugin_enabled(db, "apps_inventory")
    if get_setting_value(db, "plugin.mqtt-hass.enabled", get_setting_value(db, "plugin.mqtt.enabled", "false")) != "true":
        raise HTTPException(status_code=404, detail="Feature is disabled")
    try:
        publish_asset_updates_action(db, manual=True)
    except AssetActionAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=f"Asset action is already running: {exc.action}") from exc
    return RedirectResponse(url="/assets", status_code=303)


@router.get("/assets/system/{system_id}")
def asset_page(system_id: int, request: Request, show_inactive: bool = False, asset_id: int | None = None, db: Session = Depends(get_db)):
    require_plugin_enabled(db, "apps_inventory")
    system = db.query(System).filter(System.id == system_id).first()
    if system is None:
        raise HTTPException(status_code=404, detail="System not found")
    apps_query = db.query(Asset).filter(Asset.system_id == system.id)
    if not show_inactive:
        apps_query = apps_query.filter(Asset.is_active == True)
    apps = apps_query.order_by(Asset.name).all()
    app_ids = [asset.id for asset in apps]
    focused_asset = next((asset for asset in apps if asset.id == asset_id), None)
    if focused_asset is not None:
        focused_host = normalize_asset_host(focused_asset.host_url)
        events_query = db.query(Event).filter(Event.asset_id == focused_asset.id)
        if focused_host:
            host_matched_ids = [event.id for event in db.query(Event).all() if normalize_asset_host(event.hostname) == focused_host]
            if host_matched_ids:
                events_query = db.query(Event).filter(or_(Event.asset_id == focused_asset.id, Event.id.in_(host_matched_ids)))
        events = events_query.order_by(Event.event_time.desc()).limit(100).all()
    else:
        events = (
            db.query(Event)
            .filter(Event.asset_id.in_(app_ids))
            .order_by(Event.event_time.desc())
            .limit(100)
            .all()
            if app_ids
            else []
        )
    insights = (
        db.query(Insight)
        .filter(Insight.asset_id.in_(app_ids))
        .order_by(Insight.timestamp.desc())
        .limit(50)
        .all()
        if app_ids
        else []
    )
    mqtt_plugin_enabled = (
        get_setting_value(db, "plugin.mqtt.enabled", get_setting_value(db, "plugin.mqtt-hass.enabled", "false")) == "true"
    )
    return render(
        request,
        db,
        "asset.html",
        system=system,
        apps=apps,
        events=events,
        insights=insights,
        focused_asset=focused_asset,
        show_inactive=show_inactive,
        mqtt_plugin_enabled=mqtt_plugin_enabled,
        apps_master=get_setting_value(db, "plugin.apps_inventory.apps_master", get_setting_value(db, "apps_master", "opensecdash")),
    )


@router.post("/assets/{asset_id}/metadata")
def update_asset_metadata(
    asset_id: int,
    version: str = Form(""),
    release_url: str = Form(""),
    host_url: str = Form(""),
    db: Session = Depends(get_db),
):
    require_plugin_enabled(db, "apps_inventory")
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    if get_setting_value(db, "plugin.apps_inventory.apps_master", get_setting_value(db, "apps_master", "opensecdash")) != "opensecdash" or not asset.is_active:
        raise HTTPException(status_code=403, detail="Asset metadata is managed externally or inactive")
    def save_metadata() -> None:
        asset.version = version.strip()
        asset.release_url = clean_url_value(release_url) or None
        asset.host_url = clean_url_value(host_url) or None
        sync_asset_host_events(db, asset)
        refresh_asset_update(db, asset)
        if not asset.version or not asset.latest_version or not asset.release_url:
            asset.mqtt_publish_enabled = False
        db.commit()
        if asset.mqtt_publish_enabled:
            import asyncio
            asyncio.run(get_plugin_manager().export_asset_update(db, asset))

    try:
        run_asset_metadata_action(asset.id, save_metadata)
    except AssetActionAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=f"Asset action is already running: {exc.action}") from exc
    return RedirectResponse(url=f"/assets/system/{asset.system_id}?asset_id={asset.id}#asset-events", status_code=303)


@router.post("/assets/{asset_id}/mqtt")
def toggle_asset_mqtt(asset_id: int, enabled: str = Form("false"), db: Session = Depends(get_db)):
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    if not asset.version or not asset.latest_version or not asset.release_url:
        asset.mqtt_publish_enabled = False
        db.commit()
        return RedirectResponse(url=f"/assets/system/{asset.system_id}", status_code=303)
    asset.mqtt_publish_enabled = enabled == "true"
    db.commit()
    return RedirectResponse(url=f"/assets/system/{asset.system_id}", status_code=303)


@router.get("/assets/app/{asset_id}")
def app_asset_page(asset_id: int, request: Request, db: Session = Depends(get_db)):
    require_plugin_enabled(db, "apps_inventory")
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return RedirectResponse(url=f"/assets/system/{asset.system_id}?asset_id={asset.id}", status_code=303)


@router.post("/assets/import-source")
def assets_import_source_page(db: Session = Depends(get_db)):
    source_type = get_setting_value(
        db,
        "plugin.apps_inventory.source_type",
        get_setting_value(db, "plugin.assets.source_type", get_setting_value(db, "asset_source_type", "file")),
    )
    source = get_setting_value(
        db,
        "plugin.apps_inventory.source",
        get_setting_value(db, "plugin.assets.source", get_setting_value(db, "asset_source", "dev-data/apps-installed.json")),
    )
    if source:
        try:
            import_assets_source_action(db=db, source_type=source_type, source=source)
        except AssetActionAlreadyRunning as exc:
            raise HTTPException(status_code=409, detail=f"Asset action is already running: {exc.action}") from exc
    return RedirectResponse(url="/assets", status_code=303)


@router.post("/assets/refresh-updates")
def assets_refresh_updates_page(db: Session = Depends(get_db)):
    try:
        refresh_asset_updates_action(db)
    except AssetActionAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=f"Asset action is already running: {exc.action}") from exc
    return RedirectResponse(url="/assets", status_code=303)


@router.post("/assets/cleanup-inactive")
def cleanup_inactive_assets(db: Session = Depends(get_db)):
    db.query(Asset).filter(Asset.is_active == False).delete()
    db.commit()
    return RedirectResponse(url="/assets?show_inactive=true", status_code=303)


def _read_debug_log_tail(db: Session, max_bytes: int = 200_000) -> tuple[str, str]:
    if get_setting_value(db, "log_file_enabled", "true").lower() != "true":
        return "", "File logging is disabled."
    log_path = get_setting_value(db, "log_file_path", "logs/opensecdash.log")
    path = Path(log_path).expanduser()
    if not path.exists() or not path.is_file():
        return "", f"Log file not found: {log_path}"
    try:
        size = path.stat().st_size
        with path.open("rb") as file:
            if size > max_bytes:
                file.seek(-max_bytes, 2)
            data = file.read()
        text = data.decode("utf-8", errors="replace")
        if size > max_bytes:
            text = "[log truncated to last {} bytes]\n".format(max_bytes) + text
        return redact_sensitive(text), f"Included log tail from: {log_path}"
    except Exception as exc:
        return "", f"Could not read log file {log_path}: {exc}"


def _debug_file(title: str, lines: list[str]) -> str:
    return "\n".join([title, "=" * len(title), *lines, ""])


def build_debug_report_files(db: Session) -> dict[str, str]:
    generated_at = utc_now().isoformat()
    log_text, log_status = _read_debug_log_tail(db)
    plugins = db.query(PluginRecord).order_by(PluginRecord.id).all()
    enabled_plugins = {plugin.id: is_plugin_enabled(db, plugin.id) for plugin in plugins}
    return {
        "README.txt": _debug_file(
            "OpenSecDash Debug Package",
            [
                _debug_line("Generated at", generated_at),
                _debug_line("Python", platform.python_version()),
                _debug_line("Platform", platform.platform()),
                "",
                "Redaction notice",
                "----------------",
                "OpenSecDash has already redacted known sensitive values in this package, including passwords, tokens, API keys, access keys, bearer credentials, URL usernames, and sensitive URL query parameters.",
                "Please still review every file before attaching the ZIP to a public GitHub issue. Internal hostnames, public IPs, asset names, and log-specific payloads may still be meaningful in your environment.",
            ],
        ),
        "settings.txt": _debug_file(
            "Settings",
            [_debug_line(setting.key, _redacted_setting_value(setting.key, setting.value)) for setting in db.query(Setting).order_by(Setting.key).all()],
        ),
        "plugins.txt": _debug_file(
            "Plugins",
            [
                _debug_line(
                    plugin.id,
                    f"name={plugin.name}; version={plugin.version}; enabled={enabled_plugins.get(plugin.id)}; status={plugin.status}; capabilities={','.join(plugin.capabilities or [])}",
                )
                for plugin in plugins
            ],
        ),
        "diagnostics.txt": _debug_file(
            "Diagnostics",
            [
                _debug_line(
                    f"{item.plugin}.{item.component}",
                    f"status={item.status}; last_run={item.last_run}; last_error={item.last_error or ''}",
                )
                for item in db.query(Diagnostic).order_by(Diagnostic.plugin, Diagnostic.component).all()
            ],
        ),
        "datasources.txt": _debug_file(
            "Datasources",
            [
                _debug_line(
                    source.name,
                    f"plugin={source.plugin_id}; enabled={source.enabled}; type={source.source_type}; status={source.status}; events={source.events_processed}; last_error={source.last_error or ''}",
                )
                for source in db.query(Datasource).order_by(Datasource.name).all()
            ],
        ),
        "database-counts.txt": _debug_file(
            "Database counts",
            [
                _debug_line("events", db.query(Event).count()),
                _debug_line("assets", db.query(Asset).count()),
                _debug_line("systems", db.query(System).count()),
                _debug_line("insights", db.query(Insight).count()),
                _debug_line("actions", db.query(Action).count()),
            ],
        ),
        "recent-actions.txt": _debug_file(
            "Recent actions",
            [
                _debug_line(
                    f"action#{action.id}",
                    f"time={action.timestamp}; type={action.action_type}; target_type={action.target_type}; target={action.target}; status={action.status}; result={action.result or ''}",
                )
                for action in db.query(Action).order_by(Action.timestamp.desc()).limit(20).all()
            ],
        ),
        "opensecdash-log.txt": _debug_file(
            "OpenSecDash log tail",
            ["Log status", "----------", redact_sensitive(log_status), "", log_text or "No log content included."],
        ),
    }


def build_debug_report(db: Session) -> str:
    files = build_debug_report_files(db)
    sections = []
    for filename, content in files.items():
        sections.extend([f"--- {filename} ---", content])
    return "\n".join(sections)


def build_debug_report_zip(db: Session) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename, content in build_debug_report_files(db).items():
            archive.writestr(filename, content)
    return buffer.getvalue()


@router.get("/diagnostics/debug-report")
def diagnostics_debug_report(db: Session = Depends(get_db)):
    content = build_debug_report_zip(db)
    filename = f"opensecdash-debug-report-{utc_now().strftime('%Y%m%d-%H%M%S')}.zip"
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/diagnostics")
def diagnostics_page(request: Request, db: Session = Depends(get_db)):
    plugins = db.query(PluginRecord).order_by(PluginRecord.id).all()
    plugin_rows = [
        {
            "plugin": plugin,
            "configuration_status": "enabled" if is_plugin_enabled(db, plugin.id) else "disabled",
        }
        for plugin in plugins
    ]
    diagnostic_rows = []
    for item in db.query(Diagnostic).order_by(Diagnostic.plugin).all():
        if item.plugin == "system":
            diagnostic_rows.append({"item": item, "effective_status": item.status, "message": item.last_error or ""})
            continue
        enabled = is_plugin_enabled(db, item.plugin)
        diagnostic_rows.append(
            {
                "item": item,
                "effective_status": item.status if enabled else "disabled",
                "message": item.last_error if enabled else "Plugin is disabled and not running.",
            }
        )
    return render(
        request,
        db,
        "diagnostics.html",
        plugin_rows=plugin_rows,
        datasources=db.query(Datasource).order_by(Datasource.name).all(),
        diagnostic_rows=diagnostic_rows,
        actions=db.query(Action).order_by(Action.timestamp.desc()).limit(20).all(),
    )


@router.get("/settings")
def settings_page(request: Request, db: Session = Depends(get_db)):
    plugin_setting_groups = get_plugin_manager().plugin_settings(db, get_setting_value(db, "language", "en"))
    plugin_settings_state = {
        setting["key"]: setting["value"]
        for group in plugin_setting_groups
        for setting in group["settings"]
    }
    return render(
        request,
        db,
        "settings.html",
        domain=get_setting_value(db, "domain", ""),
        language_setting=get_setting_value(db, "language", "en"),
        retention_days=get_setting_value(db, "retention_days", "30"),
        live_default=get_setting_value(db, "live_default", "true"),
        theme=get_setting_value(db, "theme", "auto"),
        timezone=get_setting_value(db, "timezone", "auto"),
        asset_source_type=get_setting_value(db, "asset_source_type", "file"),
        asset_source=get_setting_value(db, "asset_source", "dev-data/apps-installed.json"),
        github_token=get_setting_value(db, "github_token", ""),
        action_dry_run=get_setting_value(db, "action_dry_run", "true"),
        log_file_enabled=get_setting_value(db, "log_file_enabled", "true"),
        log_file_path=get_setting_value(db, "log_file_path", "logs/opensecdash.log"),
        log_level=get_setting_value(db, "log_level", "INFO"),
        plugin_setting_groups=plugin_setting_groups,
        plugin_settings_state=plugin_settings_state,
    )


@router.post("/settings")
async def save_settings(
    request: Request,
    domain: str = Form(""),
    language: str = Form("en"),
    retention_days: str = Form("30"),
    live_default: str = Form("true"),
    theme: str = Form("auto"),
    timezone: str = Form("auto"),
    asset_source_type: str = Form("file"),
    asset_source: str = Form(""),
    github_token: str = Form(""),
    action_dry_run: str = Form("true"),
    log_file_enabled: str = Form("false"),
    log_file_path: str = Form("logs/opensecdash.log"),
    log_level: str = Form("INFO"),
    db: Session = Depends(get_db),
):
    if language not in {"de", "en"}:
        language = "en"
    domain = clean_url_value(domain)
    if asset_source_type == "url":
        asset_source = clean_url_value(asset_source)
    for key, value in {
        "domain": domain,
        "language": language,
        "retention_days": retention_days,
        "live_default": live_default,
        "theme": theme,
        "timezone": timezone,
        "asset_source_type": asset_source_type,
        "asset_source": asset_source,
        "github_token": github_token,
        "action_dry_run": action_dry_run,
        "log_file_enabled": log_file_enabled,
        "log_file_path": log_file_path,
        "log_level": log_level if log_level in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"} else "INFO",
    }.items():
        save_setting(db, key, value)

    form = await request.form()
    plugin_setting_types = {
        setting["key"]: setting["type"]
        for group in get_plugin_manager().plugin_settings(db, get_setting_value(db, "language", "en"))
        for setting in group["settings"]
    }
    plugin_source_types = {
        key: str(value)
        for key, value in form.items()
        if key.startswith("plugin.") and key.endswith(".source_type")
    }
    for key, value in form.items():
        if key.startswith("plugin."):
            text_value = str(value)
            source_type_key = key.removesuffix(".source") + ".source_type"
            if plugin_setting_types.get(key) == "url" or (key.endswith(".source") and plugin_source_types.get(source_type_key) == "url"):
                text_value = clean_url_value(text_value)
            save_setting(db, key, text_value)
    db.commit()
    configure_logging_from_db(db)
    return RedirectResponse(url="/settings", status_code=303)
