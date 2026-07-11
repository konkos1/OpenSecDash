from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Mapping, Sequence

from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.core.time import resolve_timezone, utc_now
from app.models.core import AggregationDaily
from app.models.events import Event


_DASHBOARD_PLUGIN_IDS = ("crowdsec", "geoblock_log", "traefik_log")
DASHBOARD_DELTA_PERCENT_CAP = 999


def _enabled_plugins(db: Session) -> dict[str, bool]:
    return {
        plugin_id: get_setting_value(db, f"plugin.{plugin_id}.enabled", "false") == "true"
        for plugin_id in _DASHBOARD_PLUGIN_IDS
    }


def _summary_from_event_type_rows(rows: Sequence[Any]) -> dict[str, int]:
    summary = {
        "total_events": 0,
        "access_external_events": 0,
        "access_internal_events": 0,
        "security_events": 0,
        "bans": 0,
        "geoblocks": 0,
    }
    for key_value, row_value in rows:
        key = str(key_value)
        value = int(row_value or 0)
        summary["total_events"] += value
        if key.startswith("security."):
            summary["security_events"] += value
        if key.startswith("security.ban"):
            summary["bans"] += value
        if key == "security.geoblock":
            summary["geoblocks"] += value
    return summary


def _daily_rollup_summary(db: Session, value: str) -> dict[str, int] | None:
    rows = (
        db.query(AggregationDaily.key, AggregationDaily.value)
        .filter(AggregationDaily.date == value, AggregationDaily.metric == "summary")
        .order_by(AggregationDaily.value.desc(), AggregationDaily.key.asc())
        .all()
    )
    if rows:
        return {str(key): int(row_value or 0) for key, row_value in rows}
    event_type_rows = (
        db.query(AggregationDaily.key, AggregationDaily.value)
        .filter(AggregationDaily.date == value, AggregationDaily.metric == "event_type")
        .order_by(AggregationDaily.value.desc(), AggregationDaily.key.asc())
        .all()
    )
    if event_type_rows:
        return _summary_from_event_type_rows(event_type_rows)
    return None


def dashboard_today_rollup_key(since: datetime, now: datetime | None = None) -> str | None:
    """Return today's daily-rollup key when it exactly matches the UI day."""
    current = now or utc_now()
    utc_day_start = current.replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    if since == utc_day_start:
        return since.strftime("%Y-%m-%d")
    return None


def dashboard_yesterday_rollup_key(timezone_name: str | None, now: datetime | None = None) -> str:
    timezone = resolve_timezone(timezone_name)
    current = now or utc_now()
    return (current.astimezone(timezone) - timedelta(days=1)).strftime("%Y-%m-%d")


def dashboard_metric_counts(
    db: Session,
    enabled_plugins: Mapping[str, bool],
    start: datetime,
    end: datetime | None = None,
) -> dict[str, int]:
    """Count dashboard metrics without loading matching events into Python."""

    def in_range(query):
        query = query.filter(Event.event_time >= start)
        if end is not None:
            query = query.filter(Event.event_time < end)
        return query

    bans = (
        in_range(db.query(Event).filter(Event.event_type.startswith("security.ban"), Event.plugin == "crowdsec")).count()
        if enabled_plugins["crowdsec"]
        else 0
    )
    geoblocks = (
        in_range(db.query(Event).filter(Event.event_type == "security.geoblock", Event.plugin == "geoblock_log")).count()
        if enabled_plugins["geoblock_log"]
        else 0
    )
    access_external_events = 0
    access_internal_events = 0
    if enabled_plugins["traefik_log"]:
        def access_count(is_local: bool) -> int:
            return in_range(
                db.query(Event).filter(
                    Event.event_type.startswith("access."),
                    Event.plugin == "traefik_log",
                    Event.ip.isnot(None),
                    Event.ip != "",
                    Event.is_local_ip == is_local,
                )
            ).count()

        access_internal_events = access_count(True)
        access_external_events = access_count(False)
    return {
        "bans": bans,
        "geoblocks": geoblocks,
        "access_external_events": access_external_events,
        "access_internal_events": access_internal_events,
    }


def dashboard_yesterday_summary(
    db: Session,
    timezone_name: str | None,
    since: datetime,
    enabled_plugins: Mapping[str, bool],
    now: datetime | None = None,
) -> dict[str, int]:
    """Return yesterday's dashboard metrics in the configured local-day terms."""
    yesterday_start = since - timedelta(days=1)
    yesterday_end = since
    current = now or utc_now()
    try:
        retention_days = int(get_setting_value(db, "retention_days", "30"))
    except (TypeError, ValueError):
        retention_days = 30
    retention_cutoff = current.replace(tzinfo=None) - timedelta(days=retention_days)
    if retention_days <= 0 or yesterday_start >= retention_cutoff:
        counts = dashboard_metric_counts(db, enabled_plugins, yesterday_start, yesterday_end)
        if not enabled_plugins.get("crowdsec") or not _plugin_has_data_before(db, "crowdsec", yesterday_start):
            counts.pop("bans", None)
        if not enabled_plugins.get("geoblock_log") or not _plugin_has_data_before(db, "geoblock_log", yesterday_start):
            counts.pop("geoblocks", None)
        if not enabled_plugins.get("traefik_log") or not _plugin_has_data_before(db, "traefik_log", yesterday_start):
            counts.pop("access_external_events", None)
            counts.pop("access_internal_events", None)
        return counts

    return _daily_rollup_summary(db, dashboard_yesterday_rollup_key(timezone_name, current)) or {}


def _plugin_has_data_before(db: Session, plugin: str, cutoff: datetime) -> bool:
    return db.query(Event.id).filter(Event.plugin == plugin, Event.event_time < cutoff).first() is not None


def _today_start(timezone_name: str | None, now: datetime) -> datetime:
    local_now = now.astimezone(resolve_timezone(timezone_name))
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_start.astimezone(UTC).replace(tzinfo=None)


def today_counts(db: Session) -> dict[str, int]:
    """Return today's counters using the rollup fast path where applicable."""
    timezone_name = get_setting_value(db, "timezone", "auto")
    current = utc_now()
    since = _today_start(timezone_name, current)
    rollup_key = dashboard_today_rollup_key(since, current)
    rollup_summary = _daily_rollup_summary(db, rollup_key) if rollup_key else None
    return rollup_summary or dashboard_metric_counts(db, _enabled_plugins(db), since)


def yesterday_counts(db: Session) -> dict[str, int]:
    """Return yesterday's counters using the existing retention-aware fallback."""
    timezone_name = get_setting_value(db, "timezone", "auto")
    current = utc_now()
    since = _today_start(timezone_name, current)
    return dashboard_yesterday_summary(db, timezone_name, since, _enabled_plugins(db), now=current)


def metric_delta(current: int, previous: int | None) -> dict[str, str]:
    """Format the dashboard percentage delta, including the missing baseline case."""
    previous = previous or 0
    if previous == 0:
        if current == 0:
            return {"label": "±0%", "class": "dashboard-delta-same"}
        return {"label_key": "dashboard.delta_new", "class": "dashboard-delta-up"}
    percent = round(((current - previous) / previous) * 100)
    if percent > DASHBOARD_DELTA_PERCENT_CAP:
        return {"label": f">+{DASHBOARD_DELTA_PERCENT_CAP}%", "class": "dashboard-delta-up"}
    if percent > 0:
        return {"label": f"+{percent}%", "class": "dashboard-delta-up"}
    if percent < 0:
        return {"label": f"{percent}%", "class": "dashboard-delta-down"}
    return {"label": "±0%", "class": "dashboard-delta-same"}


dashboard_delta = metric_delta
