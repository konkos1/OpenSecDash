"""Local, bounded Insight JSON downloads."""
from __future__ import annotations

import base64
import binascii
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, aliased

from app.database.dependencies import get_db
from app.models.core import Insight, InsightRule
from app.services.insight_catalog import BUILTIN_INSIGHT_DEFINITIONS
from app.services.insight_export import insight_display_time, serialize_insight
from app.services.insights import utc_iso
from app.web.guards import require_events_feature_enabled

router = APIRouter(prefix="/api/insights", tags=["insights"])
MAX_PERIOD = timedelta(days=30)
MAX_CURSOR_LENGTH = 1024
_TYPE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}")


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("Export times must include a timezone")
    return parsed.astimezone(UTC).replace(tzinfo=None)


@router.get("/export")
def export_insights(
    db: Session = Depends(get_db), *,
    type: str | None = None, start: str | None = None, end: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    cursor: Annotated[str | None, Query(max_length=MAX_CURSOR_LENGTH)] = None,
) -> JSONResponse:
    """Download a fixed event-time window with descending keyset pagination."""
    require_events_feature_enabled(db)
    position: tuple[datetime, int] | None = None
    try:
        if cursor is not None:
            if any(value is not None for value in (type, start, end)):
                raise ValueError("Cursor already defines the export filters")
            decoded: Any = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
            if not isinstance(decoded, dict) or set(decoded) != {"start", "end", "time", "id", "type"}:
                raise ValueError("Invalid export cursor")
            if not all(isinstance(decoded[key], str) for key in ("start", "end", "time")):
                raise ValueError("Invalid export cursor")
            if decoded["type"] is not None and not isinstance(decoded["type"], str):
                raise ValueError("Invalid export cursor")
            if decoded["id"].__class__ is not int or not 0 < decoded["id"] <= 2**63 - 1:
                raise ValueError("Invalid export cursor")
            start, end, type = decoded["start"], decoded["end"], decoded["type"]
            position = (_time(decoded["time"]), decoded["id"])
        upper = _time(end) if end is not None else datetime.now(UTC).replace(tzinfo=None)
        lower = _time(start) if start is not None else upper - timedelta(days=1)
        if not timedelta(0) < upper - lower <= MAX_PERIOD:
            raise ValueError("Export period must be positive and at most 30 days")
        if position is not None and not lower <= position[0] < upper:
            raise ValueError("Cursor lies outside the export period")
        if type is not None:
            if _TYPE_PATTERN.fullmatch(type) is None:
                raise ValueError("Invalid insight type")
            known = type in {definition.type for definition in BUILTIN_INSIGHT_DEFINITIONS}
            if not known and db.query(InsightRule.id).filter(InsightRule.rule_id == type).first() is None and db.query(Insight.id).filter(Insight.type == type).first() is None:
                raise ValueError("Unknown insight type")
    except (ValueError, TypeError, OverflowError, binascii.Error) as exc:
        raise HTTPException(status_code=422, detail="Invalid export filters or cursor") from exc
    display_time = insight_display_time()
    query = db.query(Insight).filter(display_time >= lower, display_time < upper)
    if position is not None:
        time, id = position
        query = query.filter(or_(display_time < time, and_(display_time == time, Insight.id < id)))
    query = query.order_by(display_time.desc(), Insight.id.desc())
    if type is not None:
        # Fence the indexed time window: SQLite otherwise scans the entire
        # type index, including history outside this bounded export period.
        row = aliased(Insight, query.limit(-1).subquery())
        query = (db.query(row).filter(row.type == type)
            .order_by(func.coalesce(row.occurred_at, row.timestamp).desc(), row.id.desc()))
    rows = query.limit(limit + 1).all()
    items = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = items[-1]
        token = {"start": utc_iso(lower), "end": utc_iso(upper), "type": type,
            "time": utc_iso(last.occurred_at or last.timestamp), "id": last.id}
        next_cursor = base64.urlsafe_b64encode(json.dumps(token).encode()).decode("ascii")
    return JSONResponse({"items": [serialize_insight(row, download=True) for row in items],
        "next_cursor": next_cursor, "period": {"start": utc_iso(lower), "end": utc_iso(upper)}},
        headers={"Content-Disposition": 'attachment; filename="insights.json"', "Cache-Control": "no-store"})
