from datetime import UTC, datetime

from app.api.pages import available_rollup_periods, dashboard_delta, dashboard_yesterday_rollup_key, rollup_rows, rollup_summary, summary_from_event_type_rows
from app.models.core import AggregationDaily, AggregationMonthly
from app.models.events import Event
import app.services.events as events_service
from app.services.events import cleanup_events_by_retention, compact_completed_daily_rollups, update_rollups


def test_update_rollups_adds_summary_metrics(db_session):
    update_rollups(db_session, Event(event_time=datetime(2026, 7, 2, 12), event_type="access.error", plugin="traefik_log", ip="8.8.8.8"))
    update_rollups(db_session, Event(event_time=datetime(2026, 7, 2, 12, 30), event_type="access.error", plugin="traefik_log"))
    update_rollups(db_session, Event(event_time=datetime(2026, 7, 2, 13), event_type="security.ban", plugin="crowdsec", data_json={"scenario": "ssh-bf"}))
    db_session.commit()

    rows = rollup_rows(db_session, "day", "2026-07-02", "summary")

    assert rows == [
        {"key": "total_events", "value": 3},
        {"key": "access_external_events", "value": 1},
        {"key": "bans", "value": 1},
        {"key": "security_events", "value": 1},
    ]
    assert rollup_rows(db_session, "day", "2026-07-02", "scenario") == [{"key": "ssh-bf", "value": 1}]


def test_update_rollups_updates_monthly_for_historical_completed_month_events(db_session, monkeypatch):
    monkeypatch.setattr(events_service, "utc_now", lambda: datetime(2026, 7, 2, 12))

    update_rollups(db_session, Event(event_time=datetime(2026, 6, 15, 12), event_type="access.error", plugin="traefik_log", ip="10.0.0.5"))
    update_rollups(db_session, Event(event_time=datetime(2026, 6, 16, 12), event_type="security.geoblock", plugin="geoblock_log"))
    db_session.commit()

    assert rollup_rows(db_session, "month", "2026-06", "summary") == [
        {"key": "total_events", "value": 2},
        {"key": "access_internal_events", "value": 1},
        {"key": "geoblocks", "value": 1},
        {"key": "security_events", "value": 1},
    ]


def test_compact_completed_daily_rollups_creates_monthly_and_removes_daily(db_session):
    db_session.add_all(
        [
            AggregationDaily(date="2026-06-29", metric="summary", key="total_events", value=4),
            AggregationDaily(date="2026-06-30", metric="summary", key="total_events", value=6),
            AggregationDaily(date="2026-07-01", metric="summary", key="total_events", value=2),
        ]
    )
    db_session.commit()

    assert compact_completed_daily_rollups(db_session, datetime(2026, 7, 2)) == 1
    db_session.commit()

    assert db_session.query(AggregationDaily).filter(AggregationDaily.date.like("2026-06-%")).count() == 0
    assert rollup_rows(db_session, "month", "2026-06", "summary") == [{"key": "total_events", "value": 10}]
    assert rollup_rows(db_session, "month", "2026-07", "summary") == [{"key": "total_events", "value": 2}]


def test_compact_completed_daily_rollups_does_not_overwrite_existing_monthly_rows(db_session):
    db_session.add(AggregationMonthly(month="2026-06", metric="summary", key="total_events", value=99))
    db_session.add(AggregationDaily(date="2026-06-29", metric="summary", key="total_events", value=4))
    db_session.add(AggregationDaily(date="2026-06-30", metric="summary", key="total_events", value=6))
    db_session.commit()

    assert compact_completed_daily_rollups(db_session, datetime(2026, 7, 2)) == 1
    db_session.commit()

    assert db_session.query(AggregationDaily).filter(AggregationDaily.date.like("2026-06-%")).count() == 0
    assert rollup_rows(db_session, "month", "2026-06", "summary") == [{"key": "total_events", "value": 99}]


def test_compact_completed_daily_rollups_keeps_yesterday_for_dashboard_delta(db_session):
    db_session.add_all(
        [
            AggregationDaily(date="2026-07-30", metric="summary", key="total_events", value=4),
            AggregationDaily(date="2026-07-31", metric="summary", key="total_events", value=6),
        ]
    )
    db_session.commit()

    assert compact_completed_daily_rollups(db_session, datetime(2026, 8, 1)) == 1
    db_session.commit()

    assert db_session.query(AggregationDaily).filter_by(date="2026-07-30").count() == 0
    assert rollup_rows(db_session, "day", "2026-07-31", "summary") == [{"key": "total_events", "value": 6}]
    assert rollup_rows(db_session, "month", "2026-07", "summary") == [{"key": "total_events", "value": 10}]

    assert compact_completed_daily_rollups(db_session, datetime(2026, 8, 2)) == 1
    db_session.commit()

    assert db_session.query(AggregationDaily).filter_by(date="2026-07-31").count() == 0
    assert rollup_rows(db_session, "month", "2026-07", "summary") == [{"key": "total_events", "value": 10}]


def test_dashboard_yesterday_rollup_key_uses_configured_timezone(monkeypatch):
    import app.api.pages as pages

    monkeypatch.setattr(pages, "utc_now", lambda: datetime(2026, 7, 2, 22, 30, tzinfo=UTC))

    assert dashboard_yesterday_rollup_key("Europe/Berlin") == "2026-07-02"
    assert dashboard_yesterday_rollup_key("UTC") == "2026-07-01"


def test_dashboard_delta_formats_direction_and_missing_previous():
    assert dashboard_delta(123, None) == {"label_key": "dashboard.delta_new", "class": "dashboard-delta-up"}
    assert dashboard_delta(0, None) == {"label": "±0%", "class": "dashboard-delta-same"}
    assert dashboard_delta(10, 0) == {"label_key": "dashboard.delta_new", "class": "dashboard-delta-up"}
    assert dashboard_delta(10, 10) == {"label": "±0%", "class": "dashboard-delta-same"}
    assert dashboard_delta(123, 100) == {"label": "+23%", "class": "dashboard-delta-up"}
    assert dashboard_delta(77, 100) == {"label": "-23%", "class": "dashboard-delta-down"}


def test_summary_from_event_type_rows_supports_legacy_rollups_without_summary_metrics():
    assert summary_from_event_type_rows(
        [
            {"key": "access.allowed", "value": 10},
            {"key": "access.error", "value": 3},
            {"key": "security.ban", "value": 2},
            {"key": "security.geoblock", "value": 4},
        ]
    ) == {
        "total_events": 19,
        "access_external_events": 0,
        "access_internal_events": 0,
        "security_events": 6,
        "bans": 2,
        "geoblocks": 4,
    }


def test_rollup_summary_falls_back_to_event_type_rows(db_session):
    db_session.add(AggregationDaily(date="2026-07-02", metric="event_type", key="access.error", value=3))
    db_session.add(AggregationDaily(date="2026-07-02", metric="event_type", key="security.ban", value=2))
    db_session.commit()

    assert rollup_summary(db_session, "day", "2026-07-02") == {
        "total_events": 5,
        "access_external_events": 0,
        "access_internal_events": 0,
        "security_events": 2,
        "bans": 2,
        "geoblocks": 0,
    }


def test_available_rollup_periods_includes_current_daily_month_and_monthly(db_session):
    db_session.add(AggregationDaily(date="2026-07-02", metric="summary", key="total_events", value=2))
    db_session.add(AggregationMonthly(month="2026-06", metric="summary", key="total_events", value=10))
    db_session.commit()

    assert available_rollup_periods(db_session) == (["2026-07-02"], ["2026-07", "2026-06"])


def test_retention_cleanup_keeps_daily_rollups_needed_for_current_month(db_session):
    db_session.add(Event(event_time=datetime(2026, 7, 1), timestamp=datetime(2026, 7, 1), event_type="access.error", plugin="traefik_log", retention_class="raw"))
    db_session.add(AggregationDaily(date="2026-07-01", metric="summary", key="access_external_events", value=1))
    db_session.commit()

    deleted = cleanup_events_by_retention(db_session, 1, datetime(2026, 7, 3))
    db_session.commit()

    assert deleted == 1
    assert db_session.query(Event).count() == 0
    assert rollup_rows(db_session, "month", "2026-07", "summary") == [{"key": "access_external_events", "value": 1}]


def test_retention_cleanup_compacts_completed_month_before_deleting_raw_events(db_session):
    db_session.add(Event(event_time=datetime(2026, 6, 30), timestamp=datetime(2026, 6, 30), event_type="security.geoblock", plugin="geoblock_log", retention_class="raw"))
    db_session.add(AggregationDaily(date="2026-06-30", metric="summary", key="geoblocks", value=1))
    db_session.commit()

    deleted = cleanup_events_by_retention(db_session, 1, datetime(2026, 7, 3))
    db_session.commit()

    assert deleted == 1
    assert db_session.query(AggregationDaily).filter(AggregationDaily.date.like("2026-06-%")).count() == 0
    assert rollup_rows(db_session, "month", "2026-06", "summary") == [{"key": "geoblocks", "value": 1}]
