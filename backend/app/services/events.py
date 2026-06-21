from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.core import AggregationDaily, AggregationMonthly, Insight
from app.models.events import Event


def normalize_event_time(value: Any | None = None) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if not value:
        return datetime.utcnow()
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).replace(tzinfo=None)
    except ValueError:
        return datetime.utcnow()


def classify_access_status(status_code: int | None) -> tuple[str, str]:
    if status_code is None:
        return "access.allowed", "info"
    if status_code in {401, 403}:
        return "access.denied", "warning"
    if status_code >= 400:
        return "access.error", "warning" if status_code < 500 else "error"
    return "access.allowed", "info"


def store_event(db: Session, **values: Any) -> Event:
    event_time = normalize_event_time(values.pop("event_time", values.get("timestamp")))
    created_at = datetime.utcnow()
    values.setdefault("timestamp", event_time)
    values.setdefault("created_at", created_at)
    values.setdefault("event_time", event_time)
    values.setdefault("plugin_id", values.get("plugin"))
    values.setdefault("source_id", values.get("source"))
    values.setdefault("retention_class", "raw")
    event = Event(**values)
    db.add(event)
    db.flush()
    update_rollups(db, event)
    create_rule_based_insights(db, event)
    return event


def update_rollups(db: Session, event: Event) -> None:
    day = event.event_time.strftime("%Y-%m-%d")
    month = event.event_time.strftime("%Y-%m")
    metrics: list[tuple[str, str]] = [("event_type", event.event_type)]
    if event.country:
        metrics.append(("country", event.country))
    scenario = (event.data_json or {}).get("scenario") or (event.data_json or {}).get("crowdsec_scenario")
    if scenario:
        metrics.append(("scenario", str(scenario)))

    for metric, key in metrics:
        daily = (
            db.query(AggregationDaily)
            .filter(
                AggregationDaily.date == day,
                AggregationDaily.metric == metric,
                AggregationDaily.key == key,
            )
            .first()
        )
        if daily is None:
            db.add(AggregationDaily(date=day, metric=metric, key=key, value=1))
        else:
            daily.value += 1

        monthly = (
            db.query(AggregationMonthly)
            .filter(
                AggregationMonthly.month == month,
                AggregationMonthly.metric == metric,
                AggregationMonthly.key == key,
            )
            .first()
        )
        if monthly is None:
            db.add(AggregationMonthly(month=month, metric=metric, key=key, value=1))
        else:
            monthly.value += 1


def _insight_exists(db: Session, insight_type: str, event_ids: list[int]) -> bool:
    return (
        db.query(Insight)
        .filter(Insight.type == insight_type, Insight.related_event_ids == event_ids)
        .first()
        is not None
    )


def create_rule_based_insights(db: Session, event: Event) -> None:
    if not event.ip:
        return

    window_start = event.event_time - timedelta(seconds=60)
    window_end = event.event_time + timedelta(seconds=60)

    if event.event_type in {"security.geoblock", "security.ban"}:
        access = (
            db.query(Event)
            .filter(
                Event.id != event.id,
                Event.ip == event.ip,
                Event.event_type == "access.error",
                Event.event_time >= window_start,
                Event.event_time <= window_end,
            )
            .order_by(Event.event_time.desc())
            .first()
        )
        if access:
            insight_type = "blocked_request" if event.event_type == "security.geoblock" else "ban_after_access"
            title = "404 likely caused by geoblock" if event.event_type == "security.geoblock" else "CrowdSec ban followed access errors"
            ids = [access.id, event.id]
            if not _insight_exists(db, insight_type, ids):
                db.add(
                    Insight(
                        type=insight_type,
                        confidence=0.9 if event.event_type == "security.geoblock" else 0.95,
                        level="high",
                        title=title,
                        description=f"Events from {event.ip} occurred within the v1 correlation window.",
                        related_event_ids=ids,
                        ip=event.ip,
                        asset_id=event.asset_id or access.asset_id,
                    )
                )

    if event.event_type == "access.error" and event.path and "wp-login" in event.path:
        recent_count = (
            db.query(Event)
            .filter(
                Event.path.contains("wp-login"),
                Event.event_time >= event.event_time - timedelta(minutes=5),
            )
            .count()
        )
        if recent_count >= 1:
            ids = [event.id]
            if not _insight_exists(db, "wordpress_scan", ids):
                db.add(
                    Insight(
                        type="wordpress_scan",
                        confidence=0.7,
                        level="medium",
                        title="Possible WordPress scan",
                        description=f"Request to {event.path} matched the v1 scanner rule.",
                        related_event_ids=ids,
                        ip=event.ip,
                        asset_id=event.asset_id,
                    )
                )


def parse_traefik_line(line: str) -> dict[str, Any] | None:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    status = data.get("DownstreamStatus") or data.get("OriginStatus")
    status_code = int(status) if status not in (None, "") else None
    event_type, severity = classify_access_status(status_code)
    return {
        "event_time": normalize_event_time(data.get("StartUTC") or data.get("time")),
        "source": "Traefik Access Log",
        "source_id": "traefik-access-log",
        "plugin": "access_logs",
        "plugin_id": "access_logs",
        "event_type": event_type,
        "severity": severity,
        "ip": data.get("ClientHost"),
        "hostname": data.get("RequestHost") or data.get("RequestAddr"),
        "method": data.get("RequestMethod"),
        "path": data.get("RequestPath"),
        "status_code": status_code,
        "data_json": {
            "router": data.get("RouterName"),
            "service": data.get("ServiceName"),
            "user_agent": data.get("request_User-Agent"),
        },
        "raw_data": line,
    }


def parse_geoblock_line(line: str) -> dict[str, Any] | None:
    if "GeoBlock" not in line:
        return None
    event_type = "system.startup" if "log file opened" in line or "cache" in line else "security.geoblock"
    severity = "info" if event_type == "system.startup" else "warning"
    return {
        "source": "GeoBlock Log",
        "source_id": "geoblock-log",
        "plugin": "geoblock",
        "plugin_id": "geoblock",
        "event_type": event_type,
        "severity": severity,
        "data_json": {"message": line.strip()},
        "raw_data": line,
    }


def parse_crowdsec_decision(decision: dict[str, Any], created_at: str | None = None) -> dict[str, Any]:
    event_type = "security.ban" if decision.get("type") == "ban" else "security.unban"
    return {
        "event_time": normalize_event_time(created_at),
        "source": "CrowdSec Decisions",
        "source_id": "crowdsec-decisions",
        "plugin": "crowdsec",
        "plugin_id": "crowdsec",
        "event_type": event_type,
        "severity": "error" if event_type == "security.ban" else "info",
        "ip": decision.get("value"),
        "data_json": {
            "scenario": decision.get("scenario"),
            "duration": decision.get("duration"),
            "origin": decision.get("origin"),
            "decision_id": decision.get("id"),
        },
        "raw_data": json.dumps(decision),
    }


def import_dev_events(db: Session, dev_data_dir: str = "dev-data") -> dict[str, int]:
    base = Path(dev_data_dir)
    counts = {"access": 0, "geoblock": 0, "crowdsec": 0}

    access_path = base / "traefik-access.log"
    if access_path.exists():
        for line in access_path.read_text(encoding="utf-8").splitlines()[:500]:
            parsed = parse_traefik_line(line)
            if parsed:
                store_event(db, **parsed)
                counts["access"] += 1

    geoblock_path = base / "geoblock.log"
    if geoblock_path.exists():
        for line in geoblock_path.read_text(encoding="utf-8", errors="ignore").splitlines()[:500]:
            parsed = parse_geoblock_line(line)
            if parsed:
                store_event(db, **parsed)
                counts["geoblock"] += 1

    crowdsec_path = base / "crowdsec_sample.json"
    if crowdsec_path.exists():
        data = json.loads(crowdsec_path.read_text(encoding="utf-8"))
        for item in data[:200]:
            for decision in item.get("decisions", []):
                store_event(db, **parse_crowdsec_decision(decision, item.get("created_at")))
                counts["crowdsec"] += 1

    db.commit()
    return counts


def apply_event_filters(query, filters: dict[str, Any]):
    if filters.get("event_type"):
        event_type = filters["event_type"]
        if event_type.endswith("*"):
            query = query.filter(Event.event_type.startswith(event_type[:-1]))
        else:
            query = query.filter(Event.event_type == event_type)
    for field in ["ip", "country", "severity", "source", "plugin"]:
        if filters.get(field):
            query = query.filter(getattr(Event, field) == filters[field])
    if filters.get("status_code"):
        query = query.filter(Event.status_code == int(filters["status_code"]))
    if filters.get("path"):
        query = query.filter(Event.path.contains(filters["path"]))
    if filters.get("q"):
        q = f"%{filters['q']}%"
        query = query.filter(or_(Event.ip.like(q), Event.hostname.like(q), Event.path.like(q), Event.event_type.like(q)))
    return query
