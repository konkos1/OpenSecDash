from __future__ import annotations

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.core import AggregationDaily, AggregationMonthly
from app.services.rollups import combine_rollup_values


def _top_rollup_metric(db: Session, metric: str, limit: int) -> list[tuple[str | None, int]]:
    """Return all-time top values from compacted monthly plus open daily rollups."""
    rows = list(db.query(AggregationMonthly.key, func.sum(AggregationMonthly.value)).filter(AggregationMonthly.metric == metric).group_by(AggregationMonthly.key).all())
    rows.extend(db.query(AggregationDaily.key, func.sum(AggregationDaily.value)).filter(AggregationDaily.metric == metric).group_by(AggregationDaily.key).all())
    totals = combine_rollup_values(metric, ((key, value) for key, value in rows))
    return [
        (key or None, value)
        for key, value in sorted(totals.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


def _top_daily_rollup_metric(db: Session, metric: str, limit: int, day: datetime | None = None) -> list[tuple[str | None, int]]:
    """Return top values for the current UTC day from daily rollups only."""
    day_key = (day or utc_now()).strftime("%Y-%m-%d")
    stored_rows = (
        db.query(AggregationDaily.key, func.sum(AggregationDaily.value))
        .filter(AggregationDaily.date == day_key, AggregationDaily.metric == metric)
        .group_by(AggregationDaily.key)
        .all()
    )
    rows = sorted(
        combine_rollup_values(metric, ((key, value) for key, value in stored_rows)).items(),
        key=lambda item: (-item[1], item[0]),
    )[:limit]
    return [
        (key or None, int(value or 0))
        for key, value in rows
    ]
