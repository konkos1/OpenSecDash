import threading
from datetime import UTC, datetime

from app.api.pages import (
    available_rollup_periods,
    dashboard_delta,
    dashboard_yesterday_rollup_key,
    dashboard_yesterday_summary,
    rollup_rows,
    rollup_summary,
    summary_from_event_type_rows,
)
from app.models.core import AggregationDaily, AggregationMonthly
from app.models.events import Event
from app.models.settings import Setting
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


def test_compact_completed_daily_rollups_merges_dailies_into_existing_monthly_rows(db_session):
    # Existing monthly rows must be merged into, never skipped: skipping used
    # to throw the daily counts away forever as soon as even one monthly row
    # for that month appeared early (e.g. from a partial earlier pass).
    db_session.add(AggregationMonthly(month="2026-06", metric="summary", key="total_events", value=99))
    db_session.add(AggregationDaily(date="2026-06-29", metric="summary", key="total_events", value=4))
    db_session.add(AggregationDaily(date="2026-06-30", metric="summary", key="total_events", value=6))
    db_session.commit()

    assert compact_completed_daily_rollups(db_session, datetime(2026, 7, 2)) == 1
    db_session.commit()

    assert db_session.query(AggregationDaily).filter(AggregationDaily.date.like("2026-06-%")).count() == 0
    assert rollup_rows(db_session, "month", "2026-06", "summary") == [{"key": "total_events", "value": 109}]


def test_late_events_after_compaction_are_merged_exactly_once(db_session, monkeypatch):
    # Full month-change sequence: events during June -> compaction in July ->
    # a late June event arrives (backlog import) -> next compaction pass.
    # The month view must show the exact total at EVERY point in between -
    # no lost counts (the old "skip if monthly exists" bug) and no double
    # counting (the old direct-to-monthly write in update_rollups).
    monkeypatch.setattr(events_service, "utc_now", lambda: datetime(2026, 6, 15, 12))
    for i in range(10):
        update_rollups(db_session, Event(event_time=datetime(2026, 6, 15, 12, 0, i), event_type="security.geoblock", plugin="geoblock_log"))
    db_session.commit()

    monkeypatch.setattr(events_service, "utc_now", lambda: datetime(2026, 7, 5, 10))
    compact_completed_daily_rollups(db_session, datetime(2026, 7, 5, 10))
    db_session.commit()
    assert rollup_rows(db_session, "month", "2026-06", "summary") == [
        {"key": "geoblocks", "value": 10},
        {"key": "security_events", "value": 10},
        {"key": "total_events", "value": 10},
    ]

    update_rollups(db_session, Event(event_time=datetime(2026, 6, 20, 8), event_type="security.geoblock", plugin="geoblock_log"))
    db_session.commit()
    # Visible immediately via the read-time merge of leftover daily rows...
    summary = {row["key"]: row["value"] for row in rollup_rows(db_session, "month", "2026-06", "summary")}
    assert summary["total_events"] == 11

    compact_completed_daily_rollups(db_session, datetime(2026, 7, 5, 11))
    db_session.commit()
    # ...and still exactly once after the next compaction pass merged it.
    assert db_session.query(AggregationDaily).filter(AggregationDaily.date.like("2026-06-%")).count() == 0
    summary = {row["key"]: row["value"] for row in rollup_rows(db_session, "month", "2026-06", "summary")}
    assert summary["total_events"] == 11


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


def test_concurrent_compaction_passes_do_not_double_count(tmp_path):
    # The hourly rollup loop and the hourly retention cleanup both compact,
    # from real threads. Without serialization both passes read the same
    # daily rows and both add them to the monthly rollup - the sum came out
    # doubled and the monthly rows duplicated.
    from sqlalchemy import create_engine, event as sa_event
    from sqlalchemy.orm import sessionmaker

    from app.database.base import Base
    from app.database.session import configure_sqlite_pragmas

    engine = create_engine(f"sqlite:///{tmp_path / 'compaction.db'}", connect_args={"check_same_thread": False, "timeout": 10})
    sa_event.listens_for(engine, "connect")(configure_sqlite_pragmas)
    Base.metadata.create_all(bind=engine)
    LocalSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    db = LocalSession()
    db.add(AggregationDaily(date="2026-06-15", metric="summary", key="total_events", value=10))
    db.commit()
    db.close()

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def compact() -> None:
        session = LocalSession()
        try:
            barrier.wait(timeout=5)
            compact_completed_daily_rollups(session, datetime(2026, 7, 5, 10))
        except BaseException as exc:  # noqa: BLE001 - surfaced via assert below
            errors.append(exc)
            session.rollback()
        finally:
            session.close()

    threads = [threading.Thread(target=compact) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    db = LocalSession()
    rows = db.query(AggregationMonthly).filter_by(month="2026-06").all()
    db.close()
    engine.dispose()

    assert not errors
    assert len(rows) == 1
    assert rows[0].value == 10


def test_dashboard_yesterday_rollup_key_uses_configured_timezone(monkeypatch):
    import app.api.pages as pages

    monkeypatch.setattr(pages, "utc_now", lambda: datetime(2026, 7, 2, 22, 30, tzinfo=UTC))

    assert dashboard_yesterday_rollup_key("Europe/Berlin") == "2026-07-02"
    assert dashboard_yesterday_rollup_key("UTC") == "2026-07-01"


def test_dashboard_yesterday_summary_uses_exact_local_day_when_within_retention(db_session, monkeypatch):
    import app.api.pages as pages

    # "Now" = 2026-07-02 22:30 UTC = 2026-07-03 00:30 Europe/Berlin (Berlin's "today" just started).
    monkeypatch.setattr(pages, "utc_now", lambda: datetime(2026, 7, 2, 22, 30, tzinfo=UTC))

    # This event is stored under the UTC calendar day 2026-07-01, but in Berlin
    # local time it's 2026-07-02 01:30 - i.e. Berlin-"yesterday". The UTC-bucketed
    # rollup can't see it under the "2026-07-02" key; the exact raw-event query must.
    db_session.add(Event(event_time=datetime(2026, 7, 1, 23, 30), event_type="security.ban", plugin="crowdsec"))
    db_session.commit()

    berlin_midnight_utc = datetime(2026, 7, 2, 22, 0)  # 2026-07-03 00:00 Europe/Berlin, as naive UTC
    summary = pages.dashboard_yesterday_summary(
        db_session,
        "Europe/Berlin",
        berlin_midnight_utc,
        {"crowdsec": True, "geoblock_log": False, "traefik_log": False},
    )

    assert summary["bans"] == 1


def test_dashboard_yesterday_summary_falls_back_to_rollup_outside_retention(db_session, monkeypatch):
    import app.api.pages as pages

    monkeypatch.setattr(pages, "utc_now", lambda: datetime(2026, 7, 2, 12, 0, tzinfo=UTC))
    db_session.add(Setting(key="retention_days", value="1"))
    db_session.add(AggregationDaily(date="2026-07-01", metric="summary", key="bans", value=5))
    db_session.commit()

    since = datetime(2026, 7, 2, 0, 0)
    summary = pages.dashboard_yesterday_summary(
        db_session,
        "UTC",
        since,
        {"crowdsec": True, "geoblock_log": False, "traefik_log": False},
    )

    assert summary.get("bans") == 5


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
