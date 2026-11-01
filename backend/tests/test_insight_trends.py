import json
from datetime import datetime, timedelta

import pytest

from app.models.assets import Asset
from app.models.core import Diagnostic, Insight
from app.models.events import Event
from app.models.settings import Setting
from app.services.insight_trends import run_trend_tick

NOW = datetime(2026, 10, 24, 12)
END = NOW.replace(hour=0)


@pytest.fixture()
def trend_db(db_session):
    db_session.add(Setting(key="plugin.traefik_log.enabled", value="true"))
    db_session.add(Asset(id=1, system_id=1, name="Example service"))
    db_session.commit()
    run_trend_tick(db_session, now=END - timedelta(days=14))
    return db_session


def counts(db, previous=50, current=100, *, asset_id: int | None = 1, country: str | None = "DE", event_type="access.error"):
    for count, time in ((previous, END - timedelta(days=14)), (current, END - timedelta(days=7))):
        db.add_all([Event(event_type=event_type, asset_id=asset_id, country=country,
            event_time=time, plugin="traefik_log") for _ in range(count)])
    db.commit()


@pytest.mark.parametrize("previous,current,expected", [(0,100,False), (1,20,False),
    (49,100,False), (50,99,False), (50,100,True), (100,149,False), (100,199,False), (100,200,True)])
def test_exact_thresholds(trend_db, previous, current, expected):
    counts(trend_db, previous, current)
    run_trend_tick(trend_db, now=NOW)
    assert bool(trend_db.query(Insight).count()) is expected


def test_snapshot_boundaries_dedupe_and_retention(trend_db):
    counts(trend_db)
    trend_db.add(Event(event_type="access.error", asset_id=1, event_time=END))
    trend_db.commit()
    run_trend_tick(trend_db, now=NOW)
    insight = trend_db.query(Insight).one()
    assert insight.evidence["comparison"] == {"version": 1, "previous_start": "2026-10-10T00:00:00+00:00",
        "previous_end": "2026-10-17T00:00:00+00:00", "current_start": "2026-10-17T00:00:00+00:00",
        "current_end": "2026-10-24T00:00:00+00:00", "previous_count": 50, "current_count": 100,
        "difference": 50, "factor": 2.0, "dimension": "asset", "dimension_value": 1, "coverage": "observed_only"}
    assert insight.occurred_at == END and insight.confidence == 0.7 and insight.level == "medium"
    assert insight.related_event_ids == list(range(131,151))
    original = insight.evidence
    trend_db.query(Event).delete()
    trend_db.commit()
    run_trend_tick(trend_db, now=NOW)
    assert trend_db.query(Insight).count() == 1 and insight.evidence == original
    assert "Observed" in insight.description and "logging" in insight.description


@pytest.mark.parametrize("asset_id", [None,0,999])
def test_unknown_assets_do_not_form_series(trend_db, asset_id):
    counts(trend_db, asset_id=asset_id)
    run_trend_tick(trend_db, now=NOW)
    assert trend_db.query(Insight).count() == 0


@pytest.mark.parametrize("reason", ["retention", "new", "change", "error"])
def test_inadequate_observation_skips(trend_db, reason):
    counts(trend_db)
    if reason == "retention":
        trend_db.add(Setting(key="retention_days", value="7"))
    elif reason == "new":
        trend_db.query(Setting).filter_by(key="insights.trends.sources").delete()
    elif reason == "change":
        row = trend_db.query(Setting).filter_by(key="insights.trends.sources").one()
        state = json.loads(row.value)
        state["access"]["sources"] = []
        row.value = json.dumps(state)
    else:
        trend_db.add(Diagnostic(plugin="traefik_log", component="plugin", status="error"))
    trend_db.commit()
    run_trend_tick(trend_db, now=NOW)
    assert trend_db.query(Insight).count() == 0
    assert "insufficient" in trend_db.query(Diagnostic).filter_by(component="trends").one().last_error


def test_observation_survives_restart_and_needs_full_days(trend_db):
    counts(trend_db)
    run_trend_tick(trend_db, now=END - timedelta(seconds=1))
    assert trend_db.query(Insight).count() == 0
    trend_db.expire_all()
    run_trend_tick(trend_db, now=NOW)
    assert trend_db.query(Insight).count() == 1


def test_201_groups_resume_fixed_cutoff(trend_db):
    for asset_id in range(1,202):
        if asset_id != 1:
            trend_db.add(Asset(id=asset_id, system_id=1, name=f"Example {asset_id}"))
        counts(trend_db, asset_id=asset_id)
    from sqlalchemy.orm import Session

    results = [run_trend_tick(trend_db, now=NOW)]
    for _ in range(2):
        with Session(trend_db.get_bind(), autoflush=False) as restarted:
            results.append(run_trend_tick(restarted, now=NOW + timedelta(days=1)))
    assert [r["processed"] for r in results] == [100,100,1]
    assert [r["completed"] for r in results] == [False,False,True]
    assert trend_db.query(Insight).count() == 201
    assert {i.occurred_at for i in trend_db.query(Insight)} == {END}


@pytest.mark.parametrize("country,expected", [("de",True), (None,False), ("",False),
    ("unknown",False), ("XX",False), ("ZZ",False), ("12",False)])
def test_geoblock_uses_only_raw_geoblocks_and_known_countries(trend_db, country, expected):
    trend_db.add(Setting(key="plugin.geoblock_log.enabled", value="true"))
    trend_db.commit()
    run_trend_tick(trend_db, now=END - timedelta(days=14))
    counts(trend_db, asset_id=None, country=country, event_type="security.geoblock")
    # Other country events cannot increase the GeoBlock count.
    counts(trend_db, previous=0, current=200, asset_id=None, country=country, event_type="security.ban")
    run_trend_tick(trend_db, now=NOW)
    rows = trend_db.query(Insight).all()
    assert bool(rows) is expected
    if expected:
        assert rows[0].type == "geo.geoblocks_increased" and rows[0].asset_id is None
        assert rows[0].evidence["comparison"]["current_count"] == 100
        assert rows[0].evidence["comparison"]["dimension_value"] == "DE"


def test_source_change_only_restarts_relevant_clock(trend_db):
    counts(trend_db)
    trend_db.add(Setting(key="plugin.geoblock_log.enabled", value="true"))
    trend_db.commit()
    run_trend_tick(trend_db, now=NOW)
    assert trend_db.query(Insight).one().type == "asset.access_errors_increased"
    state = json.loads(trend_db.query(Setting).filter_by(key="insights.trends.sources").one().value)
    assert state["geoblock"]["observed_since"] == "2026-10-24T12:00:00+00:00"
    assert state["access"]["observed_since"] == "2026-10-10T00:00:00+00:00"


def test_notifications_opt_in_repeat_and_backlog_protection(trend_db, monkeypatch):
    from app.models.core import Notification, NotificationRule
    from app.services import notifications

    notifications.sync_insight_notification_rules(trend_db)
    rule = trend_db.query(NotificationRule).filter_by(rule_id=notifications.insight_notification_rule_id("asset.access_errors_increased")).one()
    assert rule.enabled is False
    rule.enabled = True
    for key, value in {"notifications.enabled": "true", "notifications.smtp_host": "smtp.example.test",
        "notifications.smtp_sender": "sender@example.test", "notifications.smtp_recipient": "admin@example.test"}.items():
        trend_db.add(Setting(key=key, value=value))
    counts(trend_db)
    monkeypatch.setattr(notifications, "utc_now", lambda: END + timedelta(minutes=1))
    notifications.invalidate_rules_cache()
    try:
        run_trend_tick(trend_db, now=END + timedelta(minutes=1))
        run_trend_tick(trend_db, now=END + timedelta(minutes=2))
        assert trend_db.query(Notification).count() == 1
        # A new day's delayed evaluation still creates a finding, but no email.
        monkeypatch.setattr(notifications, "utc_now", lambda: NOW + timedelta(days=1))
        run_trend_tick(trend_db, now=NOW + timedelta(days=1))
        assert trend_db.query(Notification).count() == 1
    finally:
        notifications.invalidate_rules_cache()


def test_filtered_events_renders_retained_comparison_in_ui_timezone(trend_db):
    from types import SimpleNamespace
    from typing import Any, cast

    from app.api import pages

    counts(trend_db)
    run_trend_tick(trend_db, now=NOW)
    trend_db.query(Event).delete()
    trend_db.add(Setting(key="timezone", value="Europe/Berlin"))
    trend_db.commit()
    request = SimpleNamespace(query_params={}, url=SimpleNamespace(path="/events"), headers={},
        state=SimpleNamespace(), cookies={})
    response = pages.events_page(cast(Any, request), insight_type="asset.access_errors_increased", range="all", db=trend_db)
    html = bytes(response.body).decode()
    assert 'data-datetime-utc="2026-10-10T00:00:00+00:00"' in html
    assert "2026-10-10 02:00:00" in html
    assert "2026-10-24 02:00:00" in html
    assert "50" in html and "100" in html and "logging" in html


@pytest.mark.parametrize("field,value", [("version",True), ("extra",1), ("coverage","complete"),
    ("previous_count",0), ("factor",float("inf")), ("difference",49),
    ("current_end","2026-10-24T00:00:00"), ("dimension_value",999.0)])
def test_comparison_validation_rejects_inconsistent_snapshots(trend_db, field, value):
    from app.services.insights import validate_evidence

    counts(trend_db)
    run_trend_tick(trend_db, now=NOW)
    evidence = trend_db.query(Insight).one().evidence
    evidence = {**evidence, "comparison": {**evidence["comparison"], field: value}}
    with pytest.raises(ValueError):
        validate_evidence(evidence)


def test_current_source_failure_preserves_unfinished_cycle(trend_db):
    for asset_id in range(1,102):
        if asset_id != 1:
            trend_db.add(Asset(id=asset_id, system_id=1, name=f"Example {asset_id}"))
        counts(trend_db, asset_id=asset_id)
    assert run_trend_tick(trend_db, now=NOW)["processed"] == 100
    failure = Diagnostic(plugin="traefik_log", component="plugin", status="error")
    trend_db.add(failure)
    trend_db.commit()
    result = run_trend_tick(trend_db, now=NOW)
    assert result["processed"] == 0 and result["completed"] is False
    failure.status = "healthy"
    trend_db.commit()
    assert run_trend_tick(trend_db, now=NOW)["processed"] == 1
    assert trend_db.query(Insight).count() == 101


def test_source_id_change_resets_observation(trend_db):
    from app.models.core import Datasource

    counts(trend_db)
    trend_db.add(Datasource(name="Example access log", plugin_id="traefik_log", enabled=True, status="healthy"))
    trend_db.commit()
    run_trend_tick(trend_db, now=NOW)
    assert trend_db.query(Insight).count() == 0
    state = json.loads(trend_db.query(Setting).filter_by(key="insights.trends.sources").one().value)
    assert "source:traefik_log:1" in state["access"]["sources"]
    assert state["access"]["observed_since"] == "2026-10-24T12:00:00+00:00"


def test_catalog_requires_matching_producer_and_notifications_stay_disabled(trend_db):
    from app.models.core import NotificationRule
    from app.services.insight_catalog import available_insight_types
    from app.services.notifications import sync_insight_notification_rules

    sync_insight_notification_rules(trend_db)
    assert "asset.access_errors_increased" in available_insight_types(trend_db)
    assert "geo.geoblocks_increased" not in available_insight_types(trend_db)
    assert all(not row.enabled for row in trend_db.query(NotificationRule))


def test_unlimited_retention_and_same_examples_on_new_day(trend_db):
    trend_db.add(Setting(key="retention_days", value="0"))
    counts(trend_db)
    run_trend_tick(trend_db, now=NOW)
    first = trend_db.query(Insight).one()
    # Shift the raw window without importing another sample: daily identity is not sample identity.
    for event in trend_db.query(Event):
        event.event_time += timedelta(days=1)
    trend_db.commit()
    run_trend_tick(trend_db, now=NOW + timedelta(days=1))
    rows = trend_db.query(Insight).all()
    assert len(rows) == 2
    assert rows[0].related_event_ids == rows[1].related_event_ids
    assert first.evidence["comparison"]["current_end"] == "2026-10-24T00:00:00+00:00"


def test_geoblock_error_does_not_prevent_access_comparison(trend_db):
    counts(trend_db)
    trend_db.add(Setting(key="plugin.geoblock_log.enabled", value="true"))
    trend_db.add(Diagnostic(plugin="geoblock_log", component="plugin", status="error"))
    trend_db.commit()
    result = run_trend_tick(trend_db, now=NOW)
    assert trend_db.query(Insight).one().type == "asset.access_errors_increased"
    assert result["completed"] is False
