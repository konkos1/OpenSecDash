from __future__ import annotations

import logging
import re
import threading
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import String, and_, cast, func, or_
from sqlalchemy.orm import Session

# Re-exported for existing importers; the implementation lives in core so the
# events model can derive its is_local_ip column default from it without a
# models -> services import cycle.
from app.core.net import is_local_ip_value  # noqa: F401
from app.core.time import utc_now
from app.models.core import AggregationDaily, AggregationMonthly, Insight
from app.models.events import Event
from app.services.asset_hosts import find_asset_by_host
from app.services.insight_rules import apply_declarative_insight_rules


logger = logging.getLogger(__name__)


def _apply_assumed_timezone(naive_value: datetime, assume_tz: str | None) -> datetime:
    if not assume_tz or assume_tz == "UTC":
        return naive_value
    try:
        zone = ZoneInfo(assume_tz)
    except ZoneInfoNotFoundError:
        return naive_value
    return naive_value.replace(tzinfo=zone).astimezone(UTC).replace(tzinfo=None)


def normalize_event_time(value: Any | None = None, assume_tz: str | None = None) -> datetime:
    """Normalize incoming timestamps to naive UTC for DB compatibility.

    The UI converts these values back to the configured display timezone. Keeping
    storage normalized makes filtering and future tests deterministic.

    `assume_tz` (an IANA zone name) only matters when `value` carries no timezone
    or UTC offset at all - it says which timezone that naive timestamp should be
    read as before converting to UTC. It has no effect on values that already
    carry an explicit offset (e.g. Traefik's `StartUTC` or CrowdSec's logrus
    timestamps), since those are unambiguous already.
    """
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(UTC).replace(tzinfo=None)
        return _apply_assumed_timezone(value, assume_tz)
    if not value:
        return utc_now().replace(tzinfo=None)
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is not None:
            return parsed.astimezone(UTC).replace(tzinfo=None)
        return _apply_assumed_timezone(parsed, assume_tz)
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


# A manual ban/unban action is recorded immediately by the Action framework;
# if CrowdSec's own log later records that same decision, the crowdsec_log
# datasource plugin would otherwise re-import it as a second, separate event
# for the same IP at essentially the same time - visible as an apparent
# duplicate with different (and each incomplete) data.
CROWDSEC_BAN_EVENT_TYPES = ("security.ban", "security.ban.manual")

# The Action framework embeds its own action id into the ban reason it hands
# to CrowdSec (e.g. "Manual ban via OpenSecDash (action #42)", see
# actions.create_action), and CrowdSec echoes that text verbatim into its own
# log line for API/cscli-created decisions - confirmed against a real
# CrowdSec instance:
#   msg="(<machine>/cscli) Manual ban via OpenSecDash (action #42) by ip X : 1m ban on Ip X"
# Matching on this id is exact regardless of timing, unlike a time-window
# heuristic: a ban, followed by an unban, followed by a fresh re-ban of the
# same IP within seconds would otherwise risk merging the re-ban's log line
# into the stale first ban instead of the second one.
CROWDSEC_MANUAL_ACTION_ID_PATTERN = re.compile(r"OpenSecDash.*?\(action\s*#(\d+)\)", re.IGNORECASE)

# Fallback only: used when a crowdsec ban log line can't be correlated by
# action id (defensive - e.g. an unexpected CrowdSec log format change). 10s
# is the default poll interval; 30s comfortably covers that plus manager
# scheduling slack without risking a merge across an unrelated later ban.
CROWDSEC_BAN_DEDUPE_WINDOW = timedelta(seconds=30)


def _find_crowdsec_action_duplicate_by_id(db: Session, values: dict[str, Any]) -> Event | None:
    raw_data = values.get("raw_data")
    if values.get("plugin") != "crowdsec" or not raw_data:
        return None
    match = CROWDSEC_MANUAL_ACTION_ID_PATTERN.search(raw_data)
    if not match:
        return None
    action_id = int(match.group(1))
    return (
        db.query(Event)
        .filter(Event.plugin == "crowdsec", func.json_extract(Event.data_json, "$.action_id") == action_id)
        .order_by(Event.id.asc())
        .first()
    )


def _find_recent_crowdsec_ban_duplicate(db: Session, values: dict[str, Any]) -> Event | None:
    # Only applies to incoming log-tailed re-imports ("security.ban"), never
    # to a freshly created "security.ban.manual" event: manual bans already
    # carry their own unique action id and must never be time-window-merged
    # into an earlier, unrelated manual ban just because they land close
    # together (e.g. ban -> unban -> re-ban within the fallback window).
    if values.get("plugin") != "crowdsec" or values.get("event_type") != "security.ban":
        return None
    ip = values.get("ip")
    event_time = values.get("event_time")
    if not ip or event_time is None:
        return None
    return (
        db.query(Event)
        .filter(
            Event.plugin == "crowdsec",
            Event.event_type.in_(CROWDSEC_BAN_EVENT_TYPES),
            Event.ip == ip,
            Event.event_time >= event_time - CROWDSEC_BAN_DEDUPE_WINDOW,
            Event.event_time <= event_time + CROWDSEC_BAN_DEDUPE_WINDOW,
        )
        .order_by(Event.id.asc())
        .first()
    )


def _merge_missing_fields_into_duplicate(duplicate: Event, values: dict[str, Any]) -> None:
    """Backfill fields a retained duplicate event is missing from the new values.

    Whichever of the two crowdsec ban sources (manual action vs. log-tail) is
    missing scenario/duration/country picks it up from the other, without
    ever overwriting data the retained event already has. A no-op for
    ordinary exact-match duplicates, since their fields already agree.
    """
    if not duplicate.country and values.get("country"):
        duplicate.country = values["country"]
    new_data = values.get("data_json") or {}
    if not new_data:
        return
    merged = dict(duplicate.data_json or {})
    changed = False
    for key in ("scenario", "duration"):
        if not merged.get(key) and new_data.get(key):
            merged[key] = new_data[key]
            changed = True
    if changed:
        duplicate.data_json = merged


def find_duplicate_event(db: Session, values: dict[str, Any]) -> Event | None:
    """Best-effort dedupe for log importers.

    Log plugins may re-read overlapping file windows after restarts. Prefer
    ``raw_data`` when available; otherwise compare the stable event fields.
    """
    action_duplicate = _find_crowdsec_action_duplicate_by_id(db, values)
    if action_duplicate is not None:
        return action_duplicate

    crowdsec_duplicate = _find_recent_crowdsec_ban_duplicate(db, values)
    if crowdsec_duplicate is not None:
        return crowdsec_duplicate

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
    """Insert one event and maintain derived data.

    This is the central ingestion path for APIs and plugins. Keep rollups,
    insights, host-to-asset mapping, and dedupe here so future tests can validate
    ingestion behavior without exercising every plugin.
    """
    event_time = normalize_event_time(values.pop("event_time", values.get("timestamp")))
    created_at = utc_now().replace(tzinfo=None)
    values.setdefault("timestamp", event_time)
    values.setdefault("created_at", created_at)
    values.setdefault("event_time", event_time)
    values.setdefault("plugin_id", values.get("plugin"))
    values.setdefault("source_id", values.get("source"))
    values.setdefault("retention_class", "raw")
    # Always derived from the IP, never caller-supplied: filters and the
    # dashboard counters rely on this column matching is_local_ip_value().
    values["is_local_ip"] = is_local_ip_value(values.get("ip"))
    if not values.get("asset_id"):
        matched_asset = find_asset_by_host(db, values.get("hostname"))
        if matched_asset is not None:
            values["asset_id"] = matched_asset.id
    # GeoIP enrichment happens out-of-band (see geoip.enrich_pending_events),
    # not here: a first-time import of a large log can otherwise mean
    # thousands of synchronous lookup HTTP calls in a row for uncached IPs,
    # which is by far the biggest single contributor to ingestion stalling.

    duplicate = find_duplicate_event(db, values)
    if duplicate is not None:
        _merge_missing_fields_into_duplicate(duplicate, values)
        setattr(duplicate, "_opensecdash_created", False)
        logger.debug("Skipped duplicate event plugin=%s type=%s ip=%s", values.get("plugin"), values.get("event_type"), values.get("ip"))
        return duplicate

    event = Event(**values)
    setattr(event, "_opensecdash_created", True)
    db.add(event)
    db.flush()
    logger.debug("Stored event id=%s plugin=%s type=%s ip=%s", event.id, event.plugin, event.event_type, event.ip)
    update_rollups(db, event)
    create_rule_based_insights(db, event)
    return event


def cleanup_duplicate_events(db: Session) -> int:
    # Runs on every startup: stream plain column tuples instead of building
    # a full ORM object per event, so start time doesn't balloon with the
    # size of the events table.
    rows = (
        db.query(
            Event.id,
            Event.plugin,
            Event.event_type,
            Event.raw_data,
            Event.event_time,
            Event.ip,
            Event.country,
            Event.hostname,
            Event.method,
            Event.path,
            Event.status_code,
            Event.asset_id,
        )
        .order_by(Event.id.asc())
        .yield_per(1000)
    )
    seen: set[tuple[Any, ...]] = set()
    duplicate_ids: list[int] = []

    for row in rows:
        if row.raw_data:
            key = ("raw", row.plugin, row.event_type, row.raw_data)
        else:
            key = (
                "composite",
                row.plugin,
                row.event_type,
                row.event_time,
                row.ip,
                row.country,
                row.hostname,
                row.method,
                row.path,
                row.status_code,
                row.asset_id,
            )
        if key in seen:
            duplicate_ids.append(row.id)
        else:
            seen.add(key)

    if not duplicate_ids:
        return 0

    for start in range(0, len(duplicate_ids), 500):
        db.query(Event).filter(Event.id.in_(duplicate_ids[start : start + 500])).delete(synchronize_session=False)
    db.query(AggregationDaily).delete(synchronize_session=False)
    db.query(AggregationMonthly).delete(synchronize_session=False)
    db.flush()

    for event in db.query(Event).order_by(Event.id.asc()).all():
        update_rollups(db, event)

    logger.info("Removed %d duplicate events and rebuilt rollups", len(duplicate_ids))
    return len(duplicate_ids)


def rollup_metrics_for_event(event: Event) -> list[tuple[str, str]]:
    event_type = event.event_type or "unknown"
    metrics: list[tuple[str, str]] = [("summary", "total_events"), ("event_type", event_type)]
    if event_type.startswith("access.") and event.ip:
        metrics.append(("summary", "access_internal_events" if is_local_ip_value(event.ip) else "access_external_events"))
    if event_type.startswith("security."):
        metrics.append(("summary", "security_events"))
    if event_type.startswith("security.ban"):
        metrics.append(("summary", "bans"))
    if event_type == "security.geoblock":
        metrics.append(("summary", "geoblocks"))
    if event.country:
        metrics.append(("country", event.country))
    scenario = (event.data_json or {}).get("scenario") or (event.data_json or {}).get("crowdsec_scenario")
    if scenario:
        metrics.append(("scenario", str(scenario)))
    return metrics


def update_rollups(db: Session, event: Event) -> None:
    # Only the daily rollup is written here - monthly rollups are maintained
    # exclusively by compact_completed_daily_rollups(), which adds each daily
    # row's counts to the monthly rollup at the moment it deletes that row
    # ("merge on delete"). Writing monthly here as well (as this used to do
    # for events from past months) double-books such events, and compaction
    # then either lost the daily-only counts or counted the direct-monthly
    # ones twice - there is no split of responsibilities between the two
    # writers that stays consistent for late-arriving events except this one.
    day = event.event_time.strftime("%Y-%m-%d")
    for metric, key in rollup_metrics_for_event(event):
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
            db.flush()
        else:
            daily.value += 1


# Serializes whole compaction passes against each other. The app-wide SQLite
# write lock only serializes individual commits - two concurrent passes (the
# hourly rollup loop and the hourly retention cleanup both compact) would each
# read the same daily rows and each add them to the monthly rollup, doubling
# the counts.
_COMPACTION_LOCK = threading.Lock()


def compact_completed_daily_rollups(db: Session, reference_time: datetime | None = None) -> int:
    with _COMPACTION_LOCK:
        compacted = _compact_completed_daily_rollups_locked(db, reference_time)
        # Commit while still holding the lock: releasing first would let the
        # next pass read the daily rows this one just merged but not yet
        # committed - and merge them into the monthly rollup a second time.
        db.commit()
        return compacted


def _compact_completed_daily_rollups_locked(db: Session, reference_time: datetime | None = None) -> int:
    now = reference_time or utc_now().replace(tzinfo=None)
    current_month = now.strftime("%Y-%m")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    months = [
        str(month)
        for (month,) in db.query(func.substr(AggregationDaily.date, 1, 7)).distinct().all()
        if month and str(month) < current_month
    ]
    compacted = 0
    for month in months:
        # "Merge on delete": exactly the daily rows deleted below are added to
        # the monthly rollup - created or incremented per metric/key. That
        # single invariant keeps monthly counts exact for every arrival order:
        # a late event for a completed month simply creates a fresh daily row
        # (update_rollups never writes monthly), which the next compaction
        # pass merges and removes. The previous "skip the month if monthly
        # rows already exist" logic silently threw away all daily counts of a
        # month whenever even one monthly row had appeared early.
        # Yesterday's daily row is exempt from deletion (the dashboard's
        # yesterday comparison needs it), so it is also exempt from merging
        # until a later pass deletes it.
        rows = (
            db.query(AggregationDaily.metric, AggregationDaily.key, func.sum(AggregationDaily.value))
            .filter(AggregationDaily.date.like(f"{month}-%"), AggregationDaily.date != yesterday)
            .group_by(AggregationDaily.metric, AggregationDaily.key)
            .all()
        )
        for metric, key, value in rows:
            monthly = (
                db.query(AggregationMonthly)
                .filter(AggregationMonthly.month == month, AggregationMonthly.metric == str(metric), AggregationMonthly.key == str(key))
                .first()
            )
            if monthly is None:
                db.add(AggregationMonthly(month=month, metric=str(metric), key=str(key), value=int(value or 0)))
            else:
                monthly.value += int(value or 0)

        deleted = (
            db.query(AggregationDaily)
            .filter(AggregationDaily.date.like(f"{month}-%"), AggregationDaily.date != yesterday)
            .delete(synchronize_session=False)
        )
        if deleted or rows:
            compacted += 1
    return compacted


def cleanup_events_by_retention(db: Session, retention_days: int, reference_time: datetime | None = None) -> int:
    """Delete raw events after rollups for their period are safe.

    Retention never deletes daily/monthly rollup rows. Completed months are
    compacted before raw events are removed; current-month daily rollups remain
    available even if raw events from early in the month fall outside retention.
    """
    if retention_days <= 0:
        return 0
    now = reference_time or utc_now().replace(tzinfo=None)
    compact_completed_daily_rollups(db, now)
    cutoff = now - timedelta(days=retention_days)
    deleted = (
        db.query(Event)
        .filter(Event.event_time < cutoff, Event.retention_class == "raw")
        .delete(synchronize_session=False)
    )
    return int(deleted or 0)


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

    apply_declarative_insight_rules(db, event)




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
        Event.city,
        Event.asn,
        Event.isp,
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
    """Apply shared Events/Access filters to a SQLAlchemy query.

    Keep this function side-effect free. It is a good target for future unit
    tests around boolean search, timezone terms, and local-IP filtering.
    """
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
    if filters.get("show_local_ips"):
        query = query.filter(Event.is_local_ip == True)  # noqa: E712
    elif filters.get("hide_local_ips"):
        query = query.filter(Event.is_local_ip == False)  # noqa: E712

    if filters.get("q"):
        q_text = str(filters["q"]).strip()
        tokens = tokenize_search_expression(q_text)
        if any(token in {"&&", "||", "(", ")"} for token in tokens):
            query = query.filter(build_search_expression(tokens, filters.get("q_utc_terms_by_term", {})))
        else:
            query = query.filter(search_term_condition(q_text, filters.get("q_utc_terms", [])))
    return query
