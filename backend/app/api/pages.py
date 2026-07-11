import asyncio
from collections import Counter
from datetime import datetime, timedelta
import io
import ipaddress
import json
import logging
from pathlib import Path
import platform
import zipfile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from urllib.parse import unquote, urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core import plugin_registry
from app.core.logging import configure_logging_from_db, redact_sensitive, redacted_setting_value
from app.core.template_context import get_setting_value
from app.core.time import utc_now
from app.core.version import get_app_version
from app.database.dependencies import get_db
from app.models.assets import Asset
from app.models.core import Action, AggregationDaily, AggregationMonthly, Datasource, Diagnostic, Insight, PluginRecord
from app.models.events import Event
from app.models.settings import Setting
from app.models.systems import System
from app.services.dashboard_metrics import (
    dashboard_delta as _dashboard_delta,
    dashboard_metric_counts as _dashboard_metric_counts,
    dashboard_today_rollup_key as _dashboard_today_rollup_key,
    dashboard_yesterday_rollup_key as _dashboard_yesterday_rollup_key,
    dashboard_yesterday_summary as _dashboard_yesterday_summary,
)
from app.services.insight_rules import debug_summary as insight_rules_debug_summary
from app.services.asset_updates import refresh_asset_update
from app.plugins.manager import get_plugin_manager
from app.services.asset_actions import (
    AssetActionAlreadyRunning,
    asset_action_running,
    refresh_asset_updates_action,
    run_asset_metadata_action,
)
from app.services.asset_hosts import matching_event_hostnames, normalize_asset_host, sync_asset_host_events
from app.services.events import apply_event_filters, is_local_ip_value, tokenize_search_expression
from app.web.dashboard import DashboardWidget, apply_layout, collect_dashboard_widgets, load_dashboard_layout
from app.web.guards import (
    assets_feature_enabled,
    events_feature_enabled,
    is_plugin_enabled,
    require_assets_feature_enabled,
    require_events_feature_enabled,
)
from app.web.render import render
from app.web.tables import (
    DEFAULT_EVENTS_COLUMNS,
    asset_links_for_events,
    clean_filter_value,
    clean_url_value,
    column_redirect_url,
    parse_snapshot_before,
    save_setting,
    save_table_columns,
    table_columns,
    today_hour_range,
    today_start,
    utc_search_terms_for_ui_time,
)

router = APIRouter(tags=["pages"])
logger = logging.getLogger(__name__)


def _debug_line(label: str, value: object = "") -> str:
    return f"{label}: {redact_sensitive(value)}"


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


def diagnostic_plugin_enabled(db: Session, plugin_id: str) -> bool:
    if plugin_id == "asset_updates":
        return assets_feature_enabled(db)
    if plugin_id == "geoip":
        return is_plugin_enabled(db, "geoip") and events_feature_enabled(db)
    if plugin_id == "insight_rules":
        return events_feature_enabled(db)
    return is_plugin_enabled(db, plugin_id)


def diagnostic_disabled_message(db: Session, plugin_id: str) -> str:
    if plugin_id == "asset_updates" and not assets_feature_enabled(db):
        return "No asset source plugin is enabled."
    if plugin_id == "geoip" and is_plugin_enabled(db, "geoip") and not events_feature_enabled(db):
        return "No event datasource plugin is enabled."
    if plugin_id == "insight_rules" and not events_feature_enabled(db):
        return "No event datasource plugin is enabled."
    return "Plugin is disabled and not running."


def diagnostic_component_visible(db: Session, item: Diagnostic) -> bool:
    if item.plugin == "crowdsec" and item.component in {"cscli", "lapi"}:
        connection_mode = get_setting_value(db, "plugin.crowdsec.connection_mode", "lapi")
        return item.component == connection_mode
    return True


def rollup_rows(db: Session, period: str, value: str, metric: str, limit: int | None = None) -> list[dict[str, str | int]]:
    if period == "month":
        # Month view = compacted monthly rows PLUS any daily rows of that
        # month that compaction hasn't merged yet (it merges a daily row's
        # counts into monthly only when deleting it, and exempts yesterday's
        # row for the dashboard delta). Summing the leftovers in at read time
        # keeps the month exact at every moment - right after a month change
        # and for late-arriving events - instead of briefly missing them.
        combined: dict[str, int] = {}
        for key, row_value in (
            db.query(AggregationMonthly.key, AggregationMonthly.value)
            .filter(AggregationMonthly.month == value, AggregationMonthly.metric == metric)
            .all()
        ):
            combined[str(key)] = combined.get(str(key), 0) + int(row_value or 0)
        for key, row_value in (
            db.query(AggregationDaily.key, func.sum(AggregationDaily.value))
            .filter(AggregationDaily.date.like(f"{value}-%"), AggregationDaily.metric == metric)
            .group_by(AggregationDaily.key)
            .all()
        ):
            combined[str(key)] = combined.get(str(key), 0) + int(row_value or 0)
        rows = sorted(combined.items(), key=lambda item: (-item[1], item[0]))
        if limit is not None:
            rows = rows[:limit]
        return [{"key": key, "value": row_value} for key, row_value in rows]

    query = (
        db.query(AggregationDaily.key, AggregationDaily.value)
        .filter(AggregationDaily.date == value, AggregationDaily.metric == metric)
        .order_by(AggregationDaily.value.desc(), AggregationDaily.key.asc())
    )
    if limit is not None:
        query = query.limit(limit)
    return [{"key": str(key), "value": int(row_value or 0)} for key, row_value in query.all()]


def available_rollup_periods(db: Session) -> tuple[list[str], list[str]]:
    days = [str(day) for (day,) in db.query(AggregationDaily.date).distinct().order_by(AggregationDaily.date.desc()).all()]
    monthly_months = {str(month) for (month,) in db.query(AggregationMonthly.month).distinct().all()}
    daily_months = {day[:7] for day in days}
    months = sorted(monthly_months | daily_months, reverse=True)
    return days, months


def summary_from_event_type_rows(rows: list[dict[str, str | int]]) -> dict[str, int]:
    summary = {
        "total_events": 0,
        "access_external_events": 0,
        "access_internal_events": 0,
        "security_events": 0,
        "bans": 0,
        "geoblocks": 0,
    }
    for row in rows:
        key = str(row["key"])
        value = int(row["value"])
        summary["total_events"] += value
        if key.startswith("security."):
            summary["security_events"] += value
        if key.startswith("security.ban"):
            summary["bans"] += value
        if key == "security.geoblock":
            summary["geoblocks"] += value
    return summary


def rollup_summary(db: Session, period: str, value: str) -> dict[str, int] | None:
    summary_rows = rollup_rows(db, period, value, "summary")
    if summary_rows:
        return {str(row["key"]): int(row["value"]) for row in summary_rows}
    event_type_rows = rollup_rows(db, period, value, "event_type")
    if event_type_rows:
        return summary_from_event_type_rows(event_type_rows)
    return None


def dashboard_yesterday_rollup_key(timezone_name: str | None) -> str:
    return _dashboard_yesterday_rollup_key(timezone_name, now=utc_now())


def dashboard_today_rollup_key(since: datetime) -> str | None:
    return _dashboard_today_rollup_key(since, now=utc_now())


def dashboard_metric_counts(
    db: Session,
    enabled_plugins: dict[str, bool],
    start: datetime,
    end: datetime | None = None,
) -> dict[str, int]:
    return _dashboard_metric_counts(db, enabled_plugins, start, end)


def dashboard_yesterday_summary(
    db: Session,
    timezone_name: str | None,
    since: datetime,
    enabled_plugins: dict[str, bool],
) -> dict[str, int]:
    return _dashboard_yesterday_summary(db, timezone_name, since, enabled_plugins, now=utc_now())


def dashboard_delta(current: int, previous: int | None) -> dict[str, str]:
    return _dashboard_delta(current, previous)


def core_dashboard_widgets(
    db: Session,
    *,
    top_countries: list[tuple[str, int]] | None = None,
    country_heatmap: list[dict[str, object]] | None = None,
    attack_hours: list[dict[str, int | str]] | None = None,
    access_hours: list[dict[str, int | str]] | None = None,
    latest_security_events: list[Event] | None = None,
    trend_rows: list[dict[str, str | int]] | None = None,
) -> list[DashboardWidget]:
    """Build core-owned dashboard descriptors."""
    widgets: list[DashboardWidget] = []
    if assets_feature_enabled(db):
        widgets.extend(
            [
                DashboardWidget(
                    id="core.assets",
                    type="counter",
                    section="assets",
                    title_key="dashboard.assets",
                    order=10,
                    value=db.query(Asset).filter(Asset.is_active == True).count(),
                    href="/assets",
                ),
                DashboardWidget(
                    id="core.updates",
                    type="counter",
                    section="assets",
                    title_key="dashboard.updates",
                    order=20,
                    value=db.query(Asset).filter(Asset.update_available == True).count(),
                    href="/assets?updates=true",
                ),
            ]
        )

    if top_countries is not None:
        widgets.append(
            DashboardWidget(
                id="core.top_countries",
                type="table",
                section="trends",
                title_key="dashboard.top_countries",
                order=10,
                rows=tuple(
                    {
                        "label": str(country),
                        "value": int(count),
                        "href": f"/events?{urlencode({'country': country, 'today': 'true'})}",
                    }
                    for country, count in top_countries
                ),
                empty_key="dashboard.no_data",
            )
        )
    if country_heatmap is not None:
        widgets.append(
            DashboardWidget(
                id="core.country_heatmap",
                type="map",
                section="trends",
                title_key="dashboard.country_heatmap",
                order=10,
                rows=tuple(country_heatmap),
                empty_key="dashboard.no_data",
            )
        )

    def hour_rows(items: list[dict[str, int | str]]) -> tuple[dict[str, int | str], ...]:
        return tuple(
            {
                "label": f"{int(item['hour']):02d}:00\u2013{(int(item['hour']) + 1) % 24:02d}:00",
                "value": int(item["count"]),
                "href": str(item["href"]),
            }
            for item in items
        )

    if attack_hours is not None:
        widgets.append(
            DashboardWidget(
                id="core.top_attack_hours",
                type="table",
                section="security",
                title_key="dashboard.top_attack_hours",
                order=10,
                rows=hour_rows(attack_hours),
                empty_key="dashboard.no_data",
            )
        )
    if access_hours is not None:
        widgets.append(
            DashboardWidget(
                id="core.top_access_hours",
                type="table",
                section="activity",
                title_key="dashboard.top_access_hours",
                order=10,
                rows=hour_rows(access_hours),
                empty_key="dashboard.no_data",
            )
        )
    if latest_security_events is not None:
        widgets.append(
            DashboardWidget(
                id="core.latest_security_events",
                type="feed",
                section="feed",
                title_key="dashboard.latest_security_events",
                order=10,
                rows=tuple(
                    {
                        "time": event.event_time,
                        "type": event.event_type,
                        "ip": event.ip or "",
                        "country": event.country or "",
                        "href": f"/events?{urlencode({'ip': event.ip, 'event_type': event.event_type}) if event.ip else urlencode({'event_type': event.event_type})}",
                    }
                    for event in latest_security_events
                ),
                empty_key="dashboard.no_security_events",
            )
        )
    if trend_rows is not None:
        widgets.append(
            DashboardWidget(
                id="core.security_events_trend",
                type="trend",
                section="trends",
                title_key="dashboard.security_events_trend",
                order=30,
                rows=tuple(trend_rows),
                empty_key="dashboard.no_data",
            )
        )
    return widgets


def dashboard_trend_rows(db: Session, end_date: str) -> list[dict[str, str | int]]:
    """Build a 30-day security-event trend from daily summary rollups."""
    end = datetime.strptime(end_date, "%Y-%m-%d")
    start = end - timedelta(days=29)
    rollup_values = {
        str(day): int(value or 0)
        for day, value in (
            db.query(AggregationDaily.date, func.sum(AggregationDaily.value))
            .filter(
                AggregationDaily.date >= start.strftime("%Y-%m-%d"),
                AggregationDaily.date <= end_date,
                AggregationDaily.metric == "summary",
                AggregationDaily.key == "security_events",
            )
            .group_by(AggregationDaily.date)
            .all()
        )
    }
    if not rollup_values:
        return []
    return [
        {"bucket": (start + timedelta(days=offset)).strftime("%Y-%m-%d"), "value": rollup_values.get((start + timedelta(days=offset)).strftime("%Y-%m-%d"), 0)}
        for offset in range(30)
    ]


def dashboard_layout_widget_ids(db: Session) -> set[str]:
    """Return the current allowlist of enabled dashboard widget ids."""
    ids = {widget.id for widget in collect_dashboard_widgets(db, core_dashboard_widgets(db))}
    country_data_plugins = [
        plugin_id
        for plugin_id in plugin_registry.ids_with_capability("datasource")
        if is_plugin_enabled(db, plugin_id)
    ]
    enabled_plugins = {
        "crowdsec": is_plugin_enabled(db, "crowdsec"),
        "geoblock_log": is_plugin_enabled(db, "geoblock_log"),
        "traefik_log": is_plugin_enabled(db, "traefik_log"),
    }
    security_data_plugins = [
        plugin_id
        for plugin_id in ["crowdsec", "geoblock_log"]
        if enabled_plugins[plugin_id]
    ]
    if country_data_plugins:
        ids.update({"core.top_countries", "core.country_heatmap"})
    if security_data_plugins:
        ids.update({"core.top_attack_hours", "core.latest_security_events", "core.security_events_trend"})
    if enabled_plugins["traefik_log"]:
        ids.add("core.top_access_hours")
    return ids


def _layout_entries_from_form(
    known_ids: set[str],
    submitted_ids: list[str],
    visible_ids: set[str],
) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    for widget_id in submitted_ids:
        if widget_id in known_ids and widget_id not in seen_ids:
            entries.append({"id": widget_id, "visible": widget_id in visible_ids})
            seen_ids.add(widget_id)
    return entries


def _move_layout_entry(entries: list[dict[str, object]], widget_id: str, direction: int) -> None:
    current_index = next((index for index, entry in enumerate(entries) if entry.get("id") == widget_id), None)
    if current_index is None:
        return
    target_index = current_index + direction
    if target_index < 0 or target_index >= len(entries):
        return
    entries[current_index], entries[target_index] = entries[target_index], entries[current_index]


def _save_dashboard_layout(db: Session, entries: list[dict[str, object]]) -> None:
    save_setting(db, "ui.dashboard_layout", json.dumps(entries, separators=(",", ":")))
    db.commit()


def _reset_dashboard_layout(db: Session) -> None:
    db.query(Setting).filter(Setting.key == "ui.dashboard_layout").delete(synchronize_session=False)
    db.commit()


@router.get("/fragments/backlog-banner")
def backlog_banner_fragment(request: Request, db: Session = Depends(get_db)):
    # Polled by every page (see backlog_banner.html) so the sitewide "still
    # catching up" banner updates its percentage and disappears on its own,
    # instead of only refreshing on the next full page navigation. Rendering
    # this tiny standalone fragment avoids re-running whatever (possibly
    # expensive) page happens to be open just to read the banner state.
    return render(request, db, "backlog_banner.html")


@router.get("/")
def dashboard_page(request: Request, db: Session = Depends(get_db)):
    timezone_name = get_setting_value(db, "timezone", "auto")
    since = today_start(db)
    enabled_plugins = {
        "json_assets": is_plugin_enabled(db, "json_assets"),
        "proxmox_assets": is_plugin_enabled(db, "proxmox_assets"),
        "crowdsec": is_plugin_enabled(db, "crowdsec"),
        "geoblock_log": is_plugin_enabled(db, "geoblock_log"),
        "traefik_log": is_plugin_enabled(db, "traefik_log"),
    }
    country_data_plugins = [
        plugin_id
        for plugin_id in plugin_registry.ids_with_capability("datasource")
        if is_plugin_enabled(db, plugin_id)
    ]

    today_rollup_key = dashboard_today_rollup_key(since)
    security_data_plugins = [
        plugin_id
        for plugin_id in ["crowdsec", "geoblock_log"]
        if enabled_plugins[plugin_id]
    ]
    top_countries: list[tuple[str, int]] = []
    attack_hours: list[dict[str, int | str]] = []
    access_hours: list[dict[str, int | str]] = []
    if country_data_plugins:
        if today_rollup_key:
            top_countries = [
                (str(row["key"]), int(row["value"]))
                for row in rollup_rows(db, "day", today_rollup_key, "country", limit=5)
            ]
        else:
            top_countries = [
                (str(country), int(count or 0))
                for country, count in (
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
            ]
    try:
        dashboard_timezone = ZoneInfo(timezone_name) if timezone_name and timezone_name != "auto" else ZoneInfo("UTC")
    except ZoneInfoNotFoundError:
        dashboard_timezone = ZoneInfo("UTC")
    dashboard_local_date = utc_now().astimezone(dashboard_timezone).strftime("%Y-%m-%d")

    def top_hours_for_plugins(plugin_ids: list[str], event_type: str, rollup_metric: str) -> list[dict[str, int | str]]:
        if not plugin_ids:
            return []
        if today_rollup_key:
            return [
                {"hour": int(row["key"]), "count": int(row["value"]), "href": f"/events?event_type={event_type}&today=true&hour={int(row['key']):02d}"}
                for row in rollup_rows(db, "day", today_rollup_key, rollup_metric, limit=5)
                if str(row["key"]).isdigit()
            ]
        # Fallback for non-UTC UI days: group by UTC minute bucket in SQL and
        # convert only the buckets (at most 1440 for a day) to the display
        # timezone, instead of streaming every single event_time through Python.
        hour_counts: Counter[int] = Counter()
        bucket = func.strftime("%Y-%m-%d %H:%M", Event.event_time)
        for bucket_text, count in (
            db.query(bucket, func.count(Event.id))
            .filter(Event.event_time >= since, Event.plugin.in_(plugin_ids), Event.event_time.isnot(None))
            .group_by(bucket)
            .all()
        ):
            try:
                bucket_start = datetime.strptime(str(bucket_text), "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("UTC"))
            except (TypeError, ValueError):
                continue
            hour_counts[bucket_start.astimezone(dashboard_timezone).hour] += int(count or 0)
        return [
            {"hour": hour, "count": count, "href": f"/events?event_type={event_type}&today=true&hour={hour:02d}"}
            for hour, count in hour_counts.most_common(5)
        ]

    attack_hours = top_hours_for_plugins(security_data_plugins, "security.*", "hour_security")
    access_hours = top_hours_for_plugins(["traefik_log"] if enabled_plugins["traefik_log"] else [], "access.*", "hour_access")
    latest_security_events: list[Event] = []
    if security_data_plugins:
        latest_security_events = (
            db.query(Event)
            .filter(Event.event_type.startswith("security."), Event.plugin.in_(security_data_plugins))
            .order_by(Event.event_time.desc())
            .limit(10)
            .all()
        )
    max_country_count = max((count for _, count in top_countries), default=0)
    country_heatmap = [
        point
        for country, count in top_countries
        if (point := country_map_point(country, count, max_country_count)) is not None
    ]
    trend_rows = dashboard_trend_rows(db, dashboard_local_date) if security_data_plugins else None
    dashboard_widgets = collect_dashboard_widgets(
        db,
        core_dashboard_widgets(
            db,
            top_countries=top_countries if country_data_plugins else None,
            country_heatmap=country_heatmap if country_data_plugins else None,
            attack_hours=attack_hours if security_data_plugins else None,
            access_hours=access_hours if enabled_plugins["traefik_log"] else None,
            latest_security_events=latest_security_events if security_data_plugins else None,
            trend_rows=trend_rows,
        ),
    )
    dashboard_layout_widgets = apply_layout(dashboard_widgets, load_dashboard_layout(db))
    visible_dashboard_widgets = [widget for widget in dashboard_layout_widgets if widget.visible]

    return render(
        request,
        db,
        "dashboard.html",
        dashboard_widgets=visible_dashboard_widgets,
        dashboard_layout_widgets=dashboard_layout_widgets,
        event_data_plugins_enabled=bool(country_data_plugins),
        enabled_plugins=enabled_plugins,
        top_countries=top_countries,
        attack_hours=attack_hours,
        access_hours=access_hours,
        today_events_href="/events?today=true",
        country_data_plugins=country_data_plugins,
        latest_security_events=latest_security_events,
        dashboard_local_date=dashboard_local_date,
    )


@router.post("/dashboard/layout")
async def save_dashboard_layout(request: Request, db: Session = Depends(get_db)):
    form = await request.form()

    def _save() -> None:
        known_ids = dashboard_layout_widget_ids(db)
        submitted_ids = [str(value) for value in form.getlist("widget_id")]
        visible_ids = {str(value) for value in form.getlist("visible")}
        entries = _layout_entries_from_form(known_ids, submitted_ids, visible_ids)
        move_up = str(form.get("move_up") or "")
        move_down = str(form.get("move_down") or "")
        if move_up:
            _move_layout_entry(entries, move_up, -1)
        elif move_down:
            _move_layout_entry(entries, move_down, 1)
        _save_dashboard_layout(db, entries)

    await asyncio.to_thread(_save)
    return RedirectResponse(url="/", status_code=303)


@router.post("/dashboard/layout/reset")
async def reset_dashboard_layout(db: Session = Depends(get_db)):
    await asyncio.to_thread(_reset_dashboard_layout, db)
    return RedirectResponse(url="/", status_code=303)


@router.get("/rollups")
def rollups_page(request: Request, db: Session = Depends(get_db)):
    require_events_feature_enabled(db)
    # No compaction here: a GET must not have write side effects, the hourly
    # background loop compacts anyway, and the month view merges leftover
    # daily rows at read time - so the numbers are exact without it.
    days, months = available_rollup_periods(db)
    requested_period = request.query_params.get("period") or "month"
    period = requested_period if requested_period in {"day", "month"} else "month"
    available_values = months if period == "month" else days
    requested_value = (request.query_params.get("value") or "").strip()
    selected_value = requested_value if requested_value in available_values else (available_values[0] if available_values else "")

    summary_rows = rollup_rows(db, period, selected_value, "summary") if selected_value else []
    event_type_rows = rollup_rows(db, period, selected_value, "event_type") if selected_value else []
    summary = {str(row["key"]): int(row["value"]) for row in summary_rows}
    if not summary and event_type_rows:
        summary = summary_from_event_type_rows(event_type_rows)
    return render(
        request,
        db,
        "rollups.html",
        period=period,
        selected_value=selected_value,
        available_days=days,
        available_months=months,
        summary=summary,
        event_type_rows=event_type_rows,
        scenario_rows=rollup_rows(db, period, selected_value, "scenario") if selected_value else [],
        country_rows=rollup_rows(db, period, selected_value, "country", limit=20) if selected_value else [],
    )


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
        for plugin_id in plugin_registry.ids_with_capability("datasource")
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
    form = await request.form()

    # In a thread: this route must stay async for request.form(), but its DB
    # write would otherwise run on the event loop - and freeze every page for
    # everyone whenever a background writer happens to hold the write lock.
    def _save() -> None:
        require_events_feature_enabled(db)
        save_table_columns(db, "ui.events.visible_columns", [str(value) for value in form.getlist("columns")], DEFAULT_EVENTS_COLUMNS)
        db.commit()

    await asyncio.to_thread(_save)
    return RedirectResponse(url=column_redirect_url(request, "/events", str(form.get("snapshot_before") or "")), status_code=303)


def _clean_ip_target(value: str | None) -> str:
    return unquote(str(value or "").strip())


def _ip_network_target(value: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network | None:
    clean_value = _clean_ip_target(value)
    if "/" not in clean_value:
        return None
    try:
        return ipaddress.ip_network(clean_value, strict=False)
    except ValueError:
        return None


def _ip_in_network(value: str | None, network: ipaddress.IPv4Network | ipaddress.IPv6Network | None) -> bool:
    clean_value = _clean_ip_target(value)
    if not clean_value or network is None:
        return False
    try:
        return ipaddress.ip_address(clean_value) in network
    except ValueError:
        try:
            return ipaddress.ip_network(clean_value, strict=False).overlaps(network)
        except ValueError:
            return False


def _events_for_ip_target(db: Session, ip: str, limit: int) -> list[Event]:
    network = _ip_network_target(ip)
    if network is None:
        return db.query(Event).filter(Event.ip == _clean_ip_target(ip)).order_by(Event.event_time.desc()).limit(limit).all()
    events: list[Event] = []
    for event in db.query(Event).filter(Event.ip.isnot(None), Event.ip != "").order_by(Event.event_time.desc()).all():
        if _ip_in_network(event.ip, network):
            events.append(event)
            if len(events) >= limit:
                break
    return events


def _insights_for_ip_target(db: Session, ip: str, limit: int) -> list[Insight]:
    network = _ip_network_target(ip)
    if network is None:
        return db.query(Insight).filter(Insight.ip == _clean_ip_target(ip)).order_by(Insight.timestamp.desc()).limit(limit).all()
    insights: list[Insight] = []
    for insight in db.query(Insight).filter(Insight.ip.isnot(None), Insight.ip != "").order_by(Insight.timestamp.desc()).all():
        if _ip_in_network(insight.ip, network):
            insights.append(insight)
            if len(insights) >= limit:
                break
    return insights


def dedupe_insights_for_display(raw_insights: list[Insight], limit: int, *, include_ip: bool = False) -> list[Insight]:
    insights = []
    seen_insight_keys = set()
    for insight in raw_insights:
        insight_key = (insight.type, insight.ip) if include_ip else insight.type
        if insight_key in seen_insight_keys:
            continue
        seen_insight_keys.add(insight_key)
        insights.append(insight)
        if len(insights) >= limit:
            break
    return insights


@router.get("/ip/{ip:path}")
def ip_explorer_page(ip: str, request: Request, db: Session = Depends(get_db)):
    require_events_feature_enabled(db)
    events = _events_for_ip_target(db, ip, 200)
    raw_insights = _insights_for_ip_target(db, ip, 100)
    insights = dedupe_insights_for_display(raw_insights, 50)
    # Count cards, extra context and panels are all contributed by plugins, so
    # the IP explorer stays free of any per-plugin knowledge.
    manager = get_plugin_manager()
    count_widgets: list[dict[str, object]] = []
    extra_context: dict[str, object] = {}
    for plugin in manager.plugins.values():
        count_widgets.extend(plugin.ip_page_count_widgets(db, ip))
        extra_context.update(plugin.ip_page_context(db, ip))
    plugin_ip_panels: list[str] = []
    for _plugin_id, registration in manager.web_registrations():
        plugin_ip_panels.extend(registration.ip_page_panels)
    action_dry_run = get_setting_value(db, "action_dry_run", "true").lower() == "true"
    available_ip_actions = manager.available_actions(db, "ip", ip)
    return render(
        request,
        db,
        "ip.html",
        ip=ip,
        events=events,
        insights=insights,
        count_widgets=count_widgets,
        local_ip_target=is_local_ip_value(ip),
        action_dry_run=action_dry_run,
        available_ip_actions=available_ip_actions,
        plugin_ip_panels=plugin_ip_panels,
        **extra_context,
    )


def _search_blob(*values: object) -> str:
    return " ".join(str(value or "") for value in values).lower()


def asset_system_matches_search(system: System, apps: list[Asset], query: str) -> bool:
    terms = [term for term in query.lower().split() if term]
    if not terms:
        return True
    system_blob = _search_blob(system.vmid, system.hostname, system.system_type)
    app_blobs = [
        _search_blob(
            app.name,
            app.host_url,
            app.version,
            app.latest_version,
            app.release_url,
            app.update_check_type,
            "active" if app.is_active else "inactive",
            "update" if app.update_available else "no update",
        )
        for app in apps
    ]
    haystack = " ".join([system_blob, *app_blobs])
    return all(term in haystack for term in terms)


def asset_stale_threshold(source_plugin: str | None) -> timedelta:
    if source_plugin == "proxmox_assets":
        return timedelta(hours=24)
    return timedelta(days=7)


def asset_last_seen_stale(last_seen: datetime | None, source_plugin: str | None, now: datetime | None = None) -> bool:
    if last_seen is None:
        return True
    current = now or utc_now().replace(tzinfo=None)
    return current - last_seen > asset_stale_threshold(source_plugin)


def _assets_url(*, show_inactive: bool, updates: bool, q: str, source: str = "") -> str:
    params = {}
    if show_inactive:
        params["show_inactive"] = "true"
    if updates:
        params["updates"] = "true"
    if q:
        params["q"] = q
    if source:
        params["source"] = source
    return "/assets" + (f"?{urlencode(params)}" if params else "")


@router.get("/assets")
def assets_page(request: Request, show_inactive: bool = False, updates: bool = False, q: str = "", source: str = "", proxmox_error: str = "", db: Session = Depends(get_db)):
    require_assets_feature_enabled(db)
    systems = db.query(System).order_by(System.hostname).all()
    system_rows = []
    clean_q = q.strip()
    clean_source = source.strip()
    source_options = [value for (value,) in db.query(System.source_plugin).distinct().order_by(System.source_plugin).all() if value]
    now = utc_now().replace(tzinfo=None)
    for system in systems:
        if clean_source and system.source_plugin != clean_source:
            continue
        apps_query = db.query(Asset).filter(Asset.system_id == system.id)
        if not show_inactive:
            apps_query = apps_query.filter(Asset.is_active == True)
        if updates:
            apps_query = apps_query.filter(Asset.update_available == True)
        apps_for_row = apps_query.all()
        if clean_q and not asset_system_matches_search(system, apps_for_row, clean_q):
            continue
        app_count = len(apps_for_row)
        if updates and app_count == 0:
            continue
        update_available = any(asset.update_available for asset in apps_for_row)
        latest_asset = max(apps_for_row, key=lambda asset: asset.last_seen or system.last_seen or datetime.min) if apps_for_row else None
        last_seen = latest_asset.last_seen if latest_asset is not None else system.last_seen
        system_rows.append(
            {
                "system": system,
                "app_count": app_count,
                "update_available": update_available,
                "last_seen": last_seen,
                "source": system.source_plugin or "manual",
                "stale": asset_last_seen_stale(last_seen, system.source_plugin, now),
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
        q=clean_q,
        source=clean_source,
        source_options=source_options,
        proxmox_error=proxmox_error.strip(),
        assets_url_all=_assets_url(show_inactive=show_inactive, updates=False, q=clean_q, source=clean_source),
        assets_url_updates=_assets_url(show_inactive=show_inactive, updates=True, q=clean_q, source=clean_source),
        assets_url_clear=_assets_url(show_inactive=show_inactive, updates=updates, q="", source=clean_source),
        assets_url_clear_source=_assets_url(show_inactive=show_inactive, updates=updates, q=clean_q),
        assets_url_hide_inactive=_assets_url(show_inactive=False, updates=updates, q=clean_q, source=clean_source),
        assets_url_show_inactive=_assets_url(show_inactive=True, updates=updates, q=clean_q, source=clean_source),
        mqtt_plugin_enabled=mqtt_plugin_enabled,
        mqtt_publishable_count=mqtt_publishable_count,
        asset_action_busy=asset_action_running(),
        asset_import_running=asset_action_running("import"),
        asset_update_check_running=asset_action_running("refresh_updates"),
        asset_mqtt_publish_running=asset_action_running("mqtt_publish"),
        json_assets_enabled=is_plugin_enabled(db, "json_assets"),
        proxmox_plugin_enabled=is_plugin_enabled(db, "proxmox_assets"),
        proxmox_sync_running=asset_action_running("proxmox_sync"),
    )


@router.get("/assets/system/{system_id}")
def asset_page(system_id: int, request: Request, show_inactive: bool = False, asset_id: int | None = None, db: Session = Depends(get_db)):
    require_assets_feature_enabled(db)
    system = db.query(System).filter(System.id == system_id).first()
    if system is None:
        raise HTTPException(status_code=404, detail="System not found")
    apps_query = db.query(Asset).filter(Asset.system_id == system.id)
    if not show_inactive:
        apps_query = apps_query.filter(Asset.is_active == True)
    apps = apps_query.order_by(Asset.name).all()
    app_ids = [asset.id for asset in apps]
    focused_asset = next((asset for asset in apps if asset.id == asset_id), None)
    host_apps: dict[str, list[Asset]] = {}
    host_labels: dict[str, str] = {}
    for app in apps:
        normalized_host = normalize_asset_host(app.host_url)
        if not normalized_host:
            continue
        host_apps.setdefault(normalized_host, []).append(app)
        host_labels.setdefault(normalized_host, app.host_url or normalized_host)
    host_event_sections = []
    # Match on DISTINCT hostnames (one per vhost) instead of scanning every
    # event row: normalization needs Python, but the resulting hostname list
    # lets SQL do the actual filtering regardless of table size.
    hostnames_by_host: dict[str, list[str]] = {}
    for (hostname,) in db.query(Event.hostname).filter(Event.hostname.isnot(None)).distinct().all():
        normalized_event_host = normalize_asset_host(hostname)
        if normalized_event_host in host_apps:
            hostnames_by_host.setdefault(normalized_event_host, []).append(hostname)
    for host, host_app_list in sorted(host_apps.items(), key=lambda item: item[0]):
        host_app_ids = [app.id for app in host_app_list]
        host_matched_hostnames = hostnames_by_host.get(host, [])
        host_events_query = db.query(Event).filter(Event.asset_id.in_(host_app_ids))
        if host_matched_hostnames:
            host_events_query = db.query(Event).filter(or_(Event.asset_id.in_(host_app_ids), Event.hostname.in_(host_matched_hostnames)))
        raw_host_insights = (
            db.query(Insight)
            .filter(Insight.asset_id.in_(host_app_ids))
            .order_by(Insight.timestamp.desc())
            .limit(100)
            .all()
        )
        host_insights = dedupe_insights_for_display(raw_host_insights, 25, include_ip=True)
        host_event_sections.append(
            {
                "host": host,
                "label": host_labels[host],
                "apps": host_app_list,
                "events": host_events_query.order_by(Event.event_time.desc()).limit(50).all(),
                "insights": host_insights,
            }
        )
    if focused_asset is not None:
        focused_host = normalize_asset_host(focused_asset.host_url)
        events_query = db.query(Event).filter(Event.asset_id == focused_asset.id)
        if focused_host:
            focused_hostnames = matching_event_hostnames(db, focused_host)
            if focused_hostnames:
                events_query = db.query(Event).filter(or_(Event.asset_id == focused_asset.id, Event.hostname.in_(focused_hostnames)))
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
    raw_insights = (
        db.query(Insight)
        .filter(Insight.asset_id.in_(app_ids))
        .order_by(Insight.timestamp.desc())
        .limit(100)
        .all()
        if app_ids
        else []
    )
    insights = dedupe_insights_for_display(raw_insights, 50, include_ip=True)
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
        host_event_sections=host_event_sections,
        insights=insights,
        focused_asset=focused_asset,
        show_inactive=show_inactive,
        mqtt_plugin_enabled=mqtt_plugin_enabled,
        apps_master=get_setting_value(db, "plugin.json_assets.apps_master", get_setting_value(db, "apps_master", "opensecdash")),
    )


@router.post("/assets/{asset_id}/metadata")
def update_asset_metadata(
    asset_id: int,
    version: str = Form(""),
    release_url: str = Form(""),
    host_url: str = Form(""),
    db: Session = Depends(get_db),
):
    require_assets_feature_enabled(db)
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    if get_setting_value(db, "plugin.json_assets.apps_master", get_setting_value(db, "apps_master", "opensecdash")) != "opensecdash" or not asset.is_active:
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
    return RedirectResponse(url=f"/assets/system/{asset.system_id}#asset-events", status_code=303)


@router.post("/assets/{asset_id}/mqtt")
def toggle_asset_mqtt(asset_id: int, enabled: str = Form("false"), db: Session = Depends(get_db)):
    require_assets_feature_enabled(db)
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
    require_assets_feature_enabled(db)
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return RedirectResponse(url=f"/assets/system/{asset.system_id}#asset-events", status_code=303)


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
        return "", "File logging is disabled. Docker installations usually write logs to stdout/stderr. Collect recent logs with: docker compose logs opensecdash --tail=500"
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
    enabled_plugins = {plugin.id: diagnostic_plugin_enabled(db, plugin.id) for plugin in plugins}
    return {
        "README.txt": _debug_file(
            "OpenSecDash Debug Package",
            [
                _debug_line("Generated at", generated_at),
                _debug_line("OpenSecDash version", get_app_version()),
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
            [_debug_line(setting.key, redacted_setting_value(setting.key, setting.value)) for setting in db.query(Setting).order_by(Setting.key).all()],
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
        "insight-rules.txt": _debug_file("Insights Engine Rules", insight_rules_debug_summary(db)),
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
    # A plugin disabled via OSD_PLUGIN_*_DISABLED is not loaded, so its stale
    # PluginRecord/Datasource/Diagnostic rows from an earlier run must not show
    # up here (its settings stay in the DB, only the UI hides it). Core
    # diagnostic components have no plugin behind them and are always shown.
    loaded_plugin_ids = set(get_plugin_manager().plugins)
    core_diagnostic_ids = {"system", "asset_updates", "insight_rules"}

    def is_visible(plugin_id: str) -> bool:
        return plugin_id in loaded_plugin_ids or plugin_id in core_diagnostic_ids

    plugins = db.query(PluginRecord).order_by(PluginRecord.id).all()
    plugin_rows = [
        {
            "plugin": plugin,
            "configuration_status": "enabled" if diagnostic_plugin_enabled(db, plugin.id) else "disabled",
        }
        for plugin in plugins
        if plugin.id in loaded_plugin_ids
    ]
    diagnostic_rows = []
    for item in db.query(Diagnostic).order_by(Diagnostic.plugin).all():
        if not is_visible(item.plugin) or not diagnostic_component_visible(db, item):
            continue
        if item.plugin == "system":
            diagnostic_rows.append({"item": item, "effective_status": item.status, "message": item.last_error or ""})
            continue
        enabled = diagnostic_plugin_enabled(db, item.plugin)
        if enabled and item.component == "plugin" and item.status == "disabled":
            diagnostic_rows.append(
                {
                    "item": item,
                    "effective_status": "warning",
                    "message": "Plugin was re-enabled; waiting for the next health check.",
                }
            )
            continue
        diagnostic_rows.append(
            {
                "item": item,
                "effective_status": item.status if enabled else "disabled",
                "message": item.last_error if enabled else diagnostic_disabled_message(db, item.plugin),
            }
        )
    datasources = [ds for ds in db.query(Datasource).order_by(Datasource.name).all() if ds.plugin_id in loaded_plugin_ids]
    return render(
        request,
        db,
        "diagnostics.html",
        plugin_rows=plugin_rows,
        datasources=datasources,
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
        log_timestamp_timezone=get_setting_value(db, "log_timestamp_timezone", "UTC"),
        live_page_refresh=get_setting_value(db, "live_page_refresh", "true"),
        update_check_enabled=get_setting_value(db, "update_check_enabled", "true"),
        asset_source_type=get_setting_value(db, "asset_source_type", "file"),
        asset_source=get_setting_value(db, "asset_source", "/assets/assets.json"),
        action_dry_run=get_setting_value(db, "action_dry_run", "true"),
        log_file_enabled=get_setting_value(db, "log_file_enabled", "true"),
        log_file_path=get_setting_value(db, "log_file_path", "logs/opensecdash.log"),
        log_level=get_setting_value(db, "log_level", "INFO"),
        asset_updates_github_token=get_setting_value(db, "asset_updates.github_token", ""),
        asset_updates_github_interval=get_setting_value(db, "asset_updates.github_interval", "21600"),
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
    log_timestamp_timezone: str = Form("UTC"),
    live_page_refresh: str = Form("true"),
    update_check_enabled: str = Form("true"),
    asset_source_type: str = Form("file"),
    asset_source: str = Form(""),
    action_dry_run: str = Form("true"),
    log_file_enabled: str = Form("false"),
    log_file_path: str = Form("logs/opensecdash.log"),
    log_level: str = Form("INFO"),
    asset_updates_github_token: str = Form(""),
    asset_updates_github_interval: str = Form("21600"),
    db: Session = Depends(get_db),
):
    form = await request.form()

    # In a thread: saving settings is the single most write-heavy request in
    # the app (dozens of setting rows plus a commit). It must stay async for
    # request.form(), but running the writes on the event loop would freeze
    # every page for everyone whenever a background writer holds the write
    # lock - which is exactly when users go to Settings to disable a plugin.
    def _save() -> None:
        nonlocal language, domain, asset_source
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
            "log_timestamp_timezone": log_timestamp_timezone,
            "live_page_refresh": live_page_refresh,
            "update_check_enabled": update_check_enabled,
            "asset_source_type": asset_source_type,
            "asset_source": asset_source,
            "action_dry_run": action_dry_run,
            "log_file_enabled": log_file_enabled,
            "log_file_path": log_file_path,
            "log_level": log_level if log_level in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"} else "INFO",
            "asset_updates.github_token": asset_updates_github_token,
            "asset_updates.github_interval": asset_updates_github_interval,
        }.items():
            save_setting(db, key, value)

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
        get_plugin_manager().refresh_health_diagnostics(db)
        configure_logging_from_db(db)

    await asyncio.to_thread(_save)
    return RedirectResponse(url="/settings", status_code=303)
