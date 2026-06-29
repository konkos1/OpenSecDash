from app.api.pages import dashboard_rollup_rows, latest_historical_rollup_day
from app.models.core import AggregationDaily


def test_dashboard_rollup_rows_returns_top_daily_aggregations(db_session):
    db_session.add_all(
        [
            AggregationDaily(date="2026-06-29", metric="event_type", key="access.error", value=12),
            AggregationDaily(date="2026-06-29", metric="event_type", key="security.ban", value=5),
            AggregationDaily(date="2026-06-29", metric="scenario", key="ssh-bf", value=7),
            AggregationDaily(date="2026-06-28", metric="event_type", key="access.error", value=99),
        ]
    )
    db_session.commit()

    rows = dashboard_rollup_rows(db_session, "2026-06-29", "event_type")

    assert rows == [
        {"key": "access.error", "value": 12},
        {"key": "security.ban", "value": 5},
    ]


def test_latest_historical_rollup_day_excludes_current_day(db_session):
    db_session.add_all(
        [
            AggregationDaily(date="2026-06-28", metric="event_type", key="access.error", value=2),
            AggregationDaily(date="2026-06-29", metric="event_type", key="access.error", value=3),
        ]
    )
    db_session.commit()

    assert latest_historical_rollup_day(db_session, "2026-06-29") == "2026-06-28"
    assert latest_historical_rollup_day(db_session, "2026-06-28") is None
