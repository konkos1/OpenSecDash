from __future__ import annotations

import ipaddress
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import String, and_, cast, or_
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.core import AggregationDaily, AggregationMonthly, Insight
from app.models.events import Event


logger = logging.getLogger(__name__)


def normalize_event_time(value: Any | None = None) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(UTC).replace(tzinfo=None)
        return value
    if not value:
        return utc_now().replace(tzinfo=None)
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is not None:
            return parsed.astimezone(UTC).replace(tzinfo=None)
        return parsed
    except ValueError:
        return utc_now().replace(tzinfo=None)


def classify_access_status(status_code: int | None) -> tuple[str, str]:
    if status_code is None:
        return "access.allowed", "info"
    if status_code in {401, 403}:
        return "access.denied", "warning"
    if status_code >= 400:
        return "access.error", "warning" if status_code < 500 else "error"
    return "access.allowed", "info"


def find_duplicate_event(db: Session, values: dict[str, Any]) -> Event | None:
    raw_data = values.get("raw_data")
    plugin = values.get("plugin", "core")
    event_type = values.get("event_type")

    if raw_data:
        return (
            db.query(Event)
            .filter(
                Event.plugin == plugin,
                Event.event_type == event_type,
                Event.raw_data == raw_data,
            )
            .order_by(Event.id.asc())
            .first()
        )

    return (
        db.query(Event)
        .filter(
            Event.plugin == plugin,
            Event.event_type == event_type,
            Event.event_time == values.get("event_time"),
            Event.ip == values.get("ip"),
            Event.country == values.get("country"),
            Event.hostname == values.get("hostname"),
            Event.method == values.get("method"),
            Event.path == values.get("path"),
            Event.status_code == values.get("status_code"),
            Event.asset_id == values.get("asset_id"),
        )
        .order_by(Event.id.asc())
        .first()
    )


def store_event(db: Session, **values: Any) -> Event:
    event_time = normalize_event_time(values.pop("event_time", values.get("timestamp")))
    created_at = utc_now().replace(tzinfo=None)
    values.setdefault("timestamp", event_time)
    values.setdefault("created_at", created_at)
    values.setdefault("event_time", event_time)
    values.setdefault("plugin_id", values.get("plugin"))
    values.setdefault("source_id", values.get("source"))
    values.setdefault("retention_class", "raw")

    duplicate = find_duplicate_event(db, values)
    if duplicate is not None:
        logger.debug("Skipped duplicate event plugin=%s type=%s ip=%s", values.get("plugin"), values.get("event_type"), values.get("ip"))
        return duplicate

    event = Event(**values)
    db.add(event)
    db.flush()
    logger.debug("Stored event id=%s plugin=%s type=%s ip=%s", event.id, event.plugin, event.event_type, event.ip)
    update_rollups(db, event)
    create_rule_based_insights(db, event)
    return event


def cleanup_duplicate_events(db: Session) -> int:
    events = db.query(Event).order_by(Event.id.asc()).all()
    seen: set[tuple[Any, ...]] = set()
    duplicate_ids: list[int] = []

    for event in events:
        if event.raw_data:
            key = ("raw", event.plugin, event.event_type, event.raw_data)
        else:
            key = (
                "composite",
                event.plugin,
                event.event_type,
                event.event_time,
                event.ip,
                event.country,
                event.hostname,
                event.method,
                event.path,
                event.status_code,
                event.asset_id,
            )
        if key in seen:
            duplicate_ids.append(event.id)
        else:
            seen.add(key)

    if not duplicate_ids:
        return 0

    db.query(Event).filter(Event.id.in_(duplicate_ids)).delete(synchronize_session=False)
    db.query(AggregationDaily).delete(synchronize_session=False)
    db.query(AggregationMonthly).delete(synchronize_session=False)
    db.flush()

    for event in db.query(Event).order_by(Event.id.asc()).all():
        update_rollups(db, event)

    logger.info("Removed %d duplicate events and rebuilt rollups", len(duplicate_ids))
    return len(duplicate_ids)


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

    ids = [event.id]
    if event.event_type == "security.geoblock" and not _insight_exists(db, "geoblock_denied_request", ids):
        country_text = f" from {event.country}" if event.country else ""
        db.add(
            Insight(
                type="geoblock_denied_request",
                confidence=0.85,
                level="high",
                title="Request denied by GeoBlock",
                description=f"GeoBlock denied a request from {event.ip}{country_text}.",
                related_event_ids=ids,
                ip=event.ip,
                asset_id=event.asset_id,
            )
        )

    if event.event_type in {"security.ban", "security.ban.manual"}:
        insight_type = "manual_security_ban" if event.event_type == "security.ban.manual" else "security_ban_observed"
        if not _insight_exists(db, insight_type, ids):
            scenario = (event.data_json or {}).get("scenario") or "unknown scenario"
            duration = (event.data_json or {}).get("duration") or "unknown duration"
            db.add(
                Insight(
                    type=insight_type,
                    confidence=0.9 if event.event_type == "security.ban.manual" else 0.85,
                    level="high",
                    title="Manual security ban" if event.event_type == "security.ban.manual" else "Security ban observed",
                    description=(
                        f"{event.ip} was manually banned via OpenSecDash."
                        if event.event_type == "security.ban.manual"
                        else f"{event.ip} was banned for {duration} due to {scenario}."
                    ),
                    related_event_ids=ids,
                    ip=event.ip,
                    asset_id=event.asset_id,
                )
            )

    window_start = event.event_time - timedelta(seconds=60)
    window_end = event.event_time + timedelta(seconds=60)

    if event.event_type in {"security.geoblock", "security.ban", "security.ban.manual"}:
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


def is_local_ip_value(value: str | None) -> bool:
    if not value:
        return False
    try:
        network = ipaddress.ip_network(str(value), strict=False)
    except ValueError:
        return False
    return (
        network.is_private
        or network.is_loopback
        or network.is_link_local
        or network.is_multicast
        or network.is_reserved
        or network.is_unspecified
    )


def searchable_event_fields():
    return [
        Event.id,
        Event.source,
        Event.source_id,
        Event.plugin,
        Event.plugin_id,
        Event.event_type,
        Event.severity,
        Event.ip,
        Event.country,
        Event.asn,
        Event.hostname,
        Event.asset_id,
        Event.method,
        Event.status_code,
        Event.path,
        Event.data_json,
        Event.raw_data,
        Event.retention_class,
    ]


def tokenize_search_expression(text: str) -> list[str]:
    tokens: list[str] = []
    i = 0
    while i < len(text):
        char = text[i]
        if char.isspace():
            i += 1
            continue
        if text.startswith("&&", i) or text.startswith("||", i):
            tokens.append(text[i : i + 2])
            i += 2
            continue
        if char in "()":
            tokens.append(char)
            i += 1
            continue
        if char in {'\"', "'"}:
            quote = char
            i += 1
            start = i
            while i < len(text) and text[i] != quote:
                i += 1
            tokens.append(text[start:i])
            i += 1 if i < len(text) else 0
            continue
        start = i
        while i < len(text) and not text[i].isspace() and text[i] not in "()":
            if text.startswith("&&", i) or text.startswith("||", i):
                break
            i += 1
        tokens.append(text[start:i])
    return [token for token in tokens if token]


def search_term_condition(term: str, extra_terms: list[str] | None = None):
    conditions = []
    original_pattern = f"%{term}%"
    conditions.extend(cast(field, String).like(original_pattern) for field in searchable_event_fields())

    # event_time is the user-visible time column. If a token looks like a date/time
    # and the UI timezone produced UTC equivalents, match event_time only against
    # those UTC equivalents. This avoids matching both the displayed local time and
    # the raw UTC database time for a query such as "03:13 && api".
    event_time_terms = extra_terms or [term]
    conditions.extend(cast(Event.event_time, String).like(f"%{current}%") for current in event_time_terms)

    if term == "-":
        conditions.extend([Event.country.is_(None), Event.country == "", Event.country == "-"])
    return or_(*conditions)


def build_search_expression(tokens: list[str], extra_terms_by_term: dict[str, list[str]] | None = None):
    position = 0
    extra_terms_by_term = extra_terms_by_term or {}

    def parse_or():
        nonlocal position
        expression = parse_and()
        while position < len(tokens) and tokens[position] == "||":
            position += 1
            expression = or_(expression, parse_and())
        return expression

    def parse_and():
        nonlocal position
        expression = parse_factor()
        while position < len(tokens) and tokens[position] == "&&":
            position += 1
            expression = and_(expression, parse_factor())
        return expression

    def parse_factor():
        nonlocal position
        if position >= len(tokens):
            return search_term_condition("")
        token = tokens[position]
        if token == "(":
            position += 1
            expression = parse_or()
            if position < len(tokens) and tokens[position] == ")":
                position += 1
            return expression
        if token == ")":
            position += 1
            return search_term_condition("")
        position += 1
        return search_term_condition(token, extra_terms_by_term.get(token, []))

    return parse_or()


def apply_event_filters(query, filters: dict[str, Any]):
    if filters.get("event_type"):
        event_type = str(filters["event_type"]).strip()
        if "*" in event_type:
            query = query.filter(Event.event_type.like(event_type.replace("*", "%")))
        elif "." in event_type:
            query = query.filter(Event.event_type == event_type)
        else:
            query = query.filter(Event.event_type.contains(event_type))

    for field in ["ip", "severity", "source", "plugin"]:
        if filters.get(field):
            query = query.filter(getattr(Event, field) == filters[field])

    if filters.get("plugins"):
        query = query.filter(Event.plugin.in_(filters["plugins"]))

    if filters.get("country"):
        country = str(filters["country"]).strip().upper()
        if country == "-":
            query = query.filter(or_(Event.country.is_(None), Event.country == "", Event.country == "-"))
        else:
            query = query.filter(Event.country == country)

    if filters.get("status_code"):
        query = query.filter(Event.status_code == int(filters["status_code"]))
    if filters.get("path"):
        query = query.filter(Event.path.contains(filters["path"]))
    if filters.get("event_time_from"):
        query = query.filter(Event.event_time >= filters["event_time_from"])
    if filters.get("event_time_to"):
        query = query.filter(Event.event_time < filters["event_time_to"])
    if filters.get("hide_local_ips"):
        ids = [event_id for event_id, ip in query.with_entities(Event.id, Event.ip).all() if not is_local_ip_value(ip)]
        query = query.filter(Event.id.in_(ids)) if ids else query.filter(False)

    if filters.get("q"):
        q_text = str(filters["q"]).strip()
        tokens = tokenize_search_expression(q_text)
        if any(token in {"&&", "||", "(", ")"} for token in tokens):
            query = query.filter(build_search_expression(tokens, filters.get("q_utc_terms_by_term", {})))
        else:
            query = query.filter(search_term_condition(q_text, filters.get("q_utc_terms", [])))
    return query
