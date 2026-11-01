"""Bounded event-time correlations over centrally stored events."""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.core import Diagnostic, Insight
from app.models.events import Event
from app.models.settings import Setting
from app.services.insight_catalog import available_insight_types
from app.services.insight_rules import InsightRule, active_rules
from app.services.insights import MAX_SAMPLE_IDS, record_insight, utc_iso

logger = logging.getLogger(__name__)

# Bound each worker tick and the late-enrichment overlap independently of raw retention.
MAX_BAN_ANCHORS = 100
MAX_CHAIN_RULES = 20
RECHECK_MINUTES = 15
CYCLE_KEY = "insights.level2.cycle"
COMPLETED_CUTOFF_KEY = "insights.level2.completed_cutoff"


def _cycle(db: Session, now: datetime) -> dict[str, Any]:
    row = db.query(Setting).filter(Setting.key == CYCLE_KEY).first()
    if row is not None:
        try:
            state = json.loads(row.value)
            fields = {"started_at", "created_from", "created_to", "last_id"}
            if not isinstance(state, dict) or set(state) not in (fields, fields | {"last_rule_id"}):
                raise ValueError("Invalid cycle fields")
            # Existing cycles resume at the first rule batch after upgrading.
            state.setdefault("last_rule_id", "")
            if not isinstance(state["last_rule_id"], str):
                raise ValueError("Invalid rule cursor")
            if type(state["last_id"]) is not int or state["last_id"] < 0:
                raise ValueError("Invalid cycle cursor")
            times = []
            for key in ("started_at", "created_from", "created_to"):
                value = datetime.fromisoformat(state[key])
                if value.utcoffset() != timedelta(0):
                    raise ValueError("Cycle timestamps must be UTC")
                times.append(value)
            if times[0] != times[2] or times[1] > times[0] - timedelta(minutes=RECHECK_MINUTES):
                raise ValueError("Invalid cycle window")
            return state
        except (ValueError, TypeError, KeyError):
            logger.warning("Discarding invalid Level 2 cycle state")
    cutoff = db.query(Setting).filter(Setting.key == COMPLETED_CUTOFF_KEY).first()
    previous = now
    if cutoff is not None:
        try:
            value = datetime.fromisoformat(cutoff.value)
            if value.utcoffset() != timedelta(0):
                raise ValueError("Completed cutoff must be UTC")
            previous = value.replace(tzinfo=None)
        except (ValueError, TypeError):
            logger.warning("Discarding invalid Level 2 completed cutoff")
    # Advance from the completed window even if draining its batches took longer than the overlap.
    upper = max(now, previous)
    return {"started_at": utc_iso(upper), "created_from": utc_iso(previous - timedelta(minutes=RECHECK_MINUTES)),
            "created_to": utc_iso(upper), "last_id": 0, "last_rule_id": ""}


def evaluate_chain(db: Session, rule: InsightRule, ban: Event) -> None:
    """Record a scan-family relationship anchored to one observed automatic ban."""
    data = ban.data_json or {}
    if (not ban.ip or ban.event_time is None or ban.event_type != "security.ban"
            or data.get("manual") or data.get("simulated") or data.get("dry_run")
            or str(data.get("result", "")).startswith("dry-run:")):
        return
    dedupe_key = f"{rule.id}:ban:{ban.id}"
    if db.query(Insight.id).filter(Insight.dedupe_key == dedupe_key).first() is not None:
        return
    options = rule.match_options or {}
    followup = options["followup_seconds"]
    matches = or_(
        *(func.lower(Event.path).contains(path.lower(), autoescape=True) for path in rule.path_contains_any),
        *(func.lower(Event.path) == path.lower() for path in options.get("path_equals_any", [])),
        *(func.lower(Event.path).startswith(path.lower(), autoescape=True) for path in options.get("path_prefix_any", [])),
    )
    probes = db.query(Event).filter(Event.event_type.in_(rule.event_types), Event.path.isnot(None), matches)
    if options.get("status_codes"):
        probes = probes.filter(Event.status_code.in_(options["status_codes"]))
    if options.get("methods"):
        probes = probes.filter(func.upper(Event.method).in_(options["methods"]))
    latest = probes.filter(Event.ip == ban.ip, Event.event_time <= ban.event_time,
        Event.event_time >= ban.event_time - timedelta(seconds=followup)).order_by(
            Event.event_time.desc(), Event.id.desc()).first()
    if latest is None:
        return
    start = latest.event_time - timedelta(minutes=rule.window_minutes)
    if db.query(Insight.id).filter(Insight.type == rule.id, Insight.correlation_key == "rule",
        Insight.occurred_at >= ban.event_time - timedelta(minutes=rule.window_minutes),
        Insight.occurred_at <= ban.event_time).first() is not None:
        return
    matching = probes.filter(Event.event_time >= start, Event.event_time <= latest.event_time)
    country = func.upper(func.trim(Event.country))
    known_country = case((
        (country.op("GLOB")("[A-Z][A-Z]")) & country.notin_(("XX", "ZZ")), country), else_=None)
    counts = matching.with_entities(func.count(Event.id),
        func.count(func.distinct(func.nullif(func.trim(Event.ip), ""))),
        func.count(func.distinct(case((Event.asset_id > 0, Event.asset_id), else_=None))),
        func.count(func.distinct(known_country))).one()
    events, ips, assets, countries = counts
    if (events < rule.threshold or ips < rule.min_distinct_ips
            or assets < options["min_distinct_assets"] or countries < options["min_distinct_countries"]):
        return
    sample = matching.with_entities(Event.id).order_by(Event.event_time.desc(), Event.id.desc()).limit(MAX_SAMPLE_IDS - 1).all()
    ids = sorted([ban.id] + [row.id for row in sample])
    gap = int((ban.event_time - latest.event_time).total_seconds())
    record_insight(db, type=rule.id, title=rule.title, level=rule.level, confidence=min(rule.confidence, 0.8),
        description=(f"{rule.description} Observed {events} matching failures from {ips} IPs across "
            f"{assets} known assets and {countries} observed countries in {rule.window_minutes} minutes. "
            f"A participating IP had an observed ban {gap} seconds after its latest matching request. "
            "Temporal proximity and GeoIP do not prove a shared campaign, the cause of the ban, "
            "successful exploitation, or a currently active ban."),
        occurred_at=ban.event_time, ip=ban.ip, asset_id=None, correlation_key="rule", dedupe_key=dedupe_key,
        related_event_ids=ids, evidence={
            "version": 1, "kind": "correlation", "rule_id": rule.id, "rule_source": rule.source,
            **({"ruleset_version": rule.ruleset_version} if rule.ruleset_version else {}),
            "window_start": utc_iso(start), "window_end": utc_iso(latest.event_time),
            "event_count": events, "distinct_ip_count": ips, "distinct_asset_count": assets,
            "distinct_country_count": countries, "followup_seconds": followup, "followup_gap_seconds": gap,
            "sample_count": len(ids), "sample_truncated": events + 1 > len(ids),
        })


def run_level2_tick(db: Session, *, now: datetime | None = None) -> dict[str, Any]:
    """Continue one fixed overlap cycle and commit its cursor with the successful batch."""
    now = now or utc_now()
    if now.tzinfo is not None:
        now = now.astimezone(UTC).replace(tzinfo=None)
    state = _cycle(db, now)
    lower = datetime.fromisoformat(state["created_from"]).replace(tzinfo=None)
    upper = datetime.fromisoformat(state["created_to"]).replace(tzinfo=None)
    available = available_insight_types(db)
    remaining_rules = [rule for rule in active_rules(db)
        if rule.kind == "scan_ban_chain" and rule.id in available and rule.id > state["last_rule_id"]]
    rules = remaining_rules[:MAX_CHAIN_RULES]
    anchors = db.query(Event).filter(Event.event_type == "security.ban", Event.ip.isnot(None), Event.ip != "",
        Event.created_at >= lower, Event.created_at <= upper, Event.id > state["last_id"]).order_by(
            Event.id).limit(MAX_BAN_ANCHORS).all()
    errors = 0
    for ban in anchors:
        for rule in rules:
            try:
                with db.begin_nested():
                    evaluate_chain(db, rule, ban)
                    db.flush()
            except Exception:
                # A bad rule/anchor cannot poison ingestion or strand all following anchors.
                errors += 1
                logger.exception("Level 2 evaluation failed for rule %s, event %s", rule.id, ban.id)
        state["last_id"] = ban.id
    rules_completed = len(anchors) < MAX_BAN_ANCHORS
    completed = rules_completed and len(remaining_rules) <= MAX_CHAIN_RULES
    if rules_completed and not completed:
        # Drain the same fixed window for every rule batch before advancing its cutoff.
        state["last_rule_id"] = rules[-1].id
        state["last_id"] = 0
    row = db.query(Setting).filter(Setting.key == CYCLE_KEY).first()
    if completed:
        cutoff = db.query(Setting).filter(Setting.key == COMPLETED_CUTOFF_KEY).first()
        if cutoff is None:
            db.add(Setting(key=COMPLETED_CUTOFF_KEY, value=state["created_to"]))
        else:
            cutoff.value = state["created_to"]
        if row is not None:
            db.delete(row)
    elif row is None:
        db.add(Setting(key=CYCLE_KEY, value=json.dumps(state)))
    else:
        row.value = json.dumps(state)
    diagnostic = db.query(Diagnostic).filter(Diagnostic.plugin == "insight_rules", Diagnostic.component == "level2").first()
    if diagnostic is None:
        diagnostic = Diagnostic(plugin="insight_rules", component="level2")
        db.add(diagnostic)
    diagnostic.status = "warning" if errors else "healthy"
    diagnostic.last_run = now
    diagnostic.last_error = f"processed={len(anchors)} rules={len(rules)} errors={errors} completed={completed}"
    db.commit()
    return {"processed": len(anchors), "errors": errors, "completed": completed}
