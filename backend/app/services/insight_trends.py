"""Fixed daily comparisons of observed raw-event counts."""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.core import plugin_registry
from app.core.template_context import get_setting_value
from app.core.time import utc_now
from app.models.assets import Asset
from app.models.core import Datasource, Diagnostic
from app.models.events import Event
from app.models.settings import Setting
from app.services.insights import MAX_SAMPLE_IDS, record_insight, utc_iso

logger = logging.getLogger(__name__)

# One hourly tick bounds work independently of the number of incoming events.
MAX_GROUPS = 100
SOURCES_KEY = "insights.trends.sources"
CYCLE_KEY = "insights.trends.cycle"
METRICS = (
    ("access", "access.error", "asset.access_errors_increased", "asset", "Observed access errors increased"),
    ("geoblock", "security.geoblock", "geo.geoblocks_increased", "country", "Observed GeoBlocks increased"),
)


def _read(db: Session, key: str) -> dict[str, Any]:
    row = db.query(Setting).filter_by(key=key).first()
    try:
        state = json.loads(row.value) if row else {}
        return state if isinstance(state, dict) else {}
    except (ValueError, TypeError):
        return {}


def _write(db: Session, key: str, state: dict[str, Any]) -> None:
    row = db.query(Setting).filter_by(key=key).first()
    if row is None:
        db.add(Setting(key=key, value=json.dumps(state)))
    else:
        row.value = json.dumps(state)


def _utc(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Invalid trend timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() != timedelta(0):
        raise ValueError("Trend timestamps must be UTC")
    return parsed.replace(tzinfo=None)


def _cycle(db: Session, today: datetime) -> dict[str, Any]:
    state = _read(db, CYCLE_KEY)
    try:
        end = _utc(state["current_end"])
        if (set(state) != {"current_end", "last_metric", "last_dimension", "completed"}
                or end != end.replace(hour=0, minute=0, second=0, microsecond=0) or end > today
                or type(state["completed"]) is not bool or state["last_metric"] not in ("", "access", "geoblock")):
            raise ValueError("Invalid trend cycle")
        cursor = state["last_dimension"]
        if state["last_metric"] == "access":
            if type(cursor) is not int or cursor < 0:
                raise ValueError("Invalid asset cursor")
        elif not isinstance(cursor, str) or (cursor and (len(cursor) != 2 or not cursor.isascii() or not cursor.isalpha() or not cursor.isupper())):
            raise ValueError("Invalid country cursor")
        if not state["completed"] or end == today:
            return state
    except (ValueError, TypeError, KeyError):
        pass
    return {"current_end": utc_iso(today), "last_metric": "", "last_dimension": "", "completed": False}


def run_trend_tick(db: Session, *, now: datetime | None = None) -> dict[str, Any]:
    """Resume a fixed daily comparison after source-specific observation checks."""
    now = now or utc_now()
    if now.tzinfo is not None:
        now = now.astimezone(UTC).replace(tzinfo=None)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    sources = _read(db, SOURCES_KEY)
    ready: dict[str, bool] = {}
    reasons = []
    failed_metrics: set[str] = set()
    try:
        retention = int(get_setting_value(db, "retention_days", "30"))
    except ValueError:
        retention = 1
    state = _cycle(db, today)
    end = _utc(state["current_end"])
    middle, start = end - timedelta(days=7), end - timedelta(days=14)
    # Cleanup uses the current time, even when resuming an older daily cycle.
    retained = retention <= 0 or start >= now - timedelta(days=retention)
    for metric, event_type, _, _, _ in METRICS:
        plugins = sorted(pid for pid in plugin_registry.ids_producing_event_type(event_type)
            if get_setting_value(db, f"plugin.{pid}.enabled", "false") == "true")
        datasources = db.query(Datasource).filter(Datasource.plugin_id.in_(plugins), Datasource.enabled == True).all()  # noqa: E712
        signature = sorted([f"plugin:{pid}" for pid in plugins] + [f"source:{row.plugin_id}:{row.id}" for row in datasources])
        prior = sources.get(metric, {})
        try:
            observed = _utc(prior["observed_since"])
            if observed > now or prior["sources"] != signature:
                raise ValueError("Changed trend sources")
        except (ValueError, TypeError, KeyError):
            observed = now
        sources[metric] = {"sources": signature, "observed_since": utc_iso(observed)}
        failed = db.query(Diagnostic.id).filter(Diagnostic.plugin.in_(plugins), Diagnostic.status.in_(("error", "warning"))).first() is not None
        failed = failed or any(row.status in ("error", "warning") for row in datasources)
        ready[metric] = bool(plugins) and retained and not failed and observed <= today - timedelta(days=14)
        causes = []
        if not plugins:
            causes.append("no enabled source")
        if not retained:
            causes.append("retention cutoff overlaps comparison window")
        if observed > today - timedelta(days=14):
            causes.append("14 complete observation days not reached")
        if failed:
            causes.append("current source error")
            failed_metrics.add(metric)
        if causes:
            reasons.append(f"{metric}: insufficient data coverage ({', '.join(causes)})")
    _write(db, SOURCES_KEY, sources)
    processed = errors = 0
    if not state["completed"]:
        for metric, event_type, insight_type, dimension, title in METRICS:
            if state["last_metric"] and metric < state["last_metric"]:
                continue
            if metric in failed_metrics:
                # Do not advance past an unfinished metric while its source is failing.
                break
            if not ready[metric]:
                continue
            # The retained cutoff must also be covered after a source reset.
            if _utc(sources[metric]["observed_since"]) > start:
                continue
            column = Event.asset_id if dimension == "asset" else func.upper(func.trim(Event.country))
            query = db.query(Event).filter(Event.event_type == event_type, Event.event_time >= start, Event.event_time < end)
            if dimension == "asset":
                query = query.filter(Event.asset_id.in_(db.query(Asset.id)))
            else:
                query = query.filter(column.op("GLOB")("[A-Z][A-Z]"), column.notin_(("XX", "ZZ")))
            cursor = state["last_dimension"] if state["last_metric"] == metric else (0 if dimension == "asset" else "")
            groups = query.filter(column > cursor).with_entities(column.label("dimension"),
                func.sum(case((Event.event_time < middle, 1), else_=0)).label("previous"),
                func.sum(case((Event.event_time >= middle, 1), else_=0)).label("current")).group_by(column).order_by(column).limit(MAX_GROUPS - processed).all()
            for group in groups:
                value, previous, current = group
                try:
                    with db.begin_nested():
                        if previous >= 50 and current >= previous * 2 and current - previous >= 50:
                            sample = query.filter(column == value, Event.event_time >= middle).with_entities(Event.id).order_by(Event.event_time.desc(), Event.id.desc()).limit(MAX_SAMPLE_IDS).all()
                            ids = [row.id for row in sample]
                            record_insight(db, type=insight_type, title=title, level="medium", confidence=0.7,
                                description=(f"Observed {event_type} counts for {dimension} {value} increased from {previous} to {current} "
                                    f"({current / previous:g} times, +{current - previous}) across two completed seven-day windows. "
                                    "Coverage is observed only; import gaps or changed logging may explain the increase. This does not establish an attack."),
                                occurred_at=end, asset_id=value if dimension == "asset" else None,
                                correlation_key=f"{dimension}:{value}", dedupe_key=f"{insight_type}:{value}:{end.date()}",
                                related_event_ids=ids, evidence={"version": 1, "kind": "trend", "sample_count": len(ids),
                                    "sample_truncated": current > len(ids), "comparison": {
                                        "version": 1, "previous_start": utc_iso(start), "previous_end": utc_iso(middle),
                                        "current_start": utc_iso(middle), "current_end": utc_iso(end),
                                        "previous_count": previous, "current_count": current, "difference": current - previous,
                                        "factor": current / previous, "dimension": dimension, "dimension_value": value,
                                        "coverage": "observed_only"}})
                except Exception:
                    errors += 1
                    logger.exception("Trend evaluation failed for %s %s", metric, value)
                processed += 1
                state.update(last_metric=metric, last_dimension=value)
            if processed == MAX_GROUPS:
                break
        else:
            state["completed"] = True
    _write(db, CYCLE_KEY, state)
    diagnostic = db.query(Diagnostic).filter_by(plugin="insight_rules", component="trends").first()
    if diagnostic is None:
        diagnostic = Diagnostic(plugin="insight_rules", component="trends")
        db.add(diagnostic)
    diagnostic.status = "warning" if reasons or errors else "healthy"
    diagnostic.last_run = now
    diagnostic.last_error = "; ".join([*reasons, f"processed={processed} errors={errors} completed={state['completed']}"])
    db.commit()
    return {"processed": processed, "errors": errors, "completed": state["completed"]}
