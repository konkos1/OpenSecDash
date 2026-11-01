from __future__ import annotations

import ipaddress
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.core import Insight
from app.services.notifications import handle_insight

# Retained evidence is a compact summary, independent of raw-event retention.
MAX_EVIDENCE_BYTES = 8 * 1024
MAX_SAMPLE_IDS = 20
_COUNT_FIELDS = {
    "event_count", "distinct_ip_count", "distinct_asset_count", "distinct_country_count",
    "sample_count", "followup_seconds", "followup_gap_seconds",
}
_TEXT_FIELDS = {"rule_id", "rule_source", "ruleset_version"}
_TIME_FIELDS = {"window_start", "window_end"}


def utc_iso(value: datetime) -> str:
    """Represent database UTC timestamps explicitly as UTC ISO strings."""
    return value.replace(tzinfo=UTC).isoformat() if value.tzinfo is None else value.astimezone(UTC).isoformat()


def ip_correlation_key(ip: str) -> str:
    """Use the canonical IP spelling for correlation identity."""
    return f"ip:{ipaddress.ip_address(ip)}"


def validate_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    """Validate the closed version-1 summary without accepting raw payloads."""
    allowed = {"version", "kind", "sample_truncated"} | _COUNT_FIELDS | _TEXT_FIELDS | _TIME_FIELDS
    if evidence.keys() - allowed:
        raise ValueError("Unknown insight evidence field")
    if type(evidence.get("version")) is not int or evidence["version"] != 1:
        raise ValueError("Unsupported insight evidence version")
    if evidence.get("kind") not in ("direct", "correlation", "heuristic", "trend"):
        raise ValueError("Invalid insight evidence kind")
    for field, value in evidence.items():
        if field in _COUNT_FIELDS and (type(value) is not int or value < 0):
            raise ValueError("Invalid insight evidence count")
        if field in _TEXT_FIELDS and (not isinstance(value, str) or not value):
            raise ValueError("Invalid insight evidence text")
        if field == "sample_truncated" and type(value) is not bool:
            raise ValueError("Invalid insight evidence sample flag")
        if field in _TIME_FIELDS:
            if not isinstance(value, str):
                raise ValueError("Invalid insight evidence time")
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            offset = parsed.utcoffset()
            if offset is None or offset.total_seconds() != 0:
                raise ValueError("Insight evidence time must be UTC")
    serialized = json.dumps(evidence, allow_nan=False)
    if len(serialized.encode("utf-8")) > MAX_EVIDENCE_BYTES:
        raise ValueError("Insight evidence exceeds 8 KiB")
    return json.loads(serialized)


def record_insight(
    db: Session, *, type: str, title: str, description: str, level: str,
    confidence: float, occurred_at: datetime, correlation_key: str | None,
    dedupe_key: str | None, related_event_ids: list[int], evidence: dict[str, Any],
    ip: str | None = None, asset_id: int | None = None,
) -> Insight:
    """Persist one bounded finding and queue notifications without committing."""
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in related_event_ids):
        raise ValueError("Invalid insight event ID")
    if len(related_event_ids) > MAX_SAMPLE_IDS:
        raise ValueError("Insight sample exceeds 20 event IDs")
    ids = sorted(set(related_event_ids))
    snapshot = validate_evidence(evidence)
    if snapshot.get("sample_count") != len(ids):
        raise ValueError("Insight sample count does not match event IDs")
    if correlation_key is not None and len(correlation_key) > 160:
        raise ValueError("Insight correlation key exceeds 160 characters")
    if dedupe_key is not None and len(dedupe_key) > 255:
        raise ValueError("Insight dedupe key exceeds 255 characters")
    if occurred_at.tzinfo is not None:
        occurred_at = occurred_at.astimezone(UTC).replace(tzinfo=None)
    existing = db.query(Insight).filter(Insight.type == type, Insight.related_event_ids == ids).first()
    if existing is not None:
        return existing
    if dedupe_key is not None:
        existing = db.query(Insight).filter(Insight.dedupe_key == dedupe_key).first()
        if existing is not None:
            return existing
    insight = Insight(type=type, title=title, description=description, level=level,
                      confidence=confidence, occurred_at=occurred_at, correlation_key=correlation_key,
                      dedupe_key=dedupe_key, related_event_ids=ids, evidence=snapshot, ip=ip, asset_id=asset_id)
    try:
        with db.begin_nested():
            db.add(insight)
            db.flush()
    except IntegrityError:
        # A competing insert may win after the pre-check. Only its key conflict
        # is handled; unrelated constraints must reach the caller's savepoint.
        existing = db.query(Insight).filter(Insight.dedupe_key == dedupe_key).first() if dedupe_key is not None else None
        if existing is None:
            raise
        return existing
    handle_insight(db, insight, occurred_at)
    return insight
