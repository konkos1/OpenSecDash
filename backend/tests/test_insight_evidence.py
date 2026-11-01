from datetime import datetime, timedelta

import pytest

from app.models.core import Insight
from app.services.events import create_rule_based_insights, store_event
from app.services.insight_rules import import_ruleset


def _probe(db, when):
    return store_event(db, source="test", plugin="traefik_log", event_type="access.error",
                       ip="192.0.2.1", event_time=when, path="/phase-two", status_code=404)


@pytest.mark.parametrize("reverse", [False, True])
def test_historical_waves_use_closed_event_time_cooldown(db_session, reverse):
    import_ruleset(db_session, {"schema_version": 1, "ruleset_version": "test", "rules": [
        {"id": "test.time", "title": "Probe", "event_types": ["access.error"],
         "path_contains_any": ["/phase-two"], "threshold": 1, "window_minutes": 5}
    ]}, source="test")
    start = datetime(2020, 1, 1)
    waves = [start, start + timedelta(days=2)]
    for when in reversed(waves) if reverse else waves:
        _probe(db_session, when)
        _probe(db_session, when + timedelta(minutes=5))
    db_session.commit()
    rows = db_session.query(Insight).filter_by(type="test.time").all()
    assert len(rows) == 2
    assert sorted(row.occurred_at for row in rows) == waves
    assert all(row.evidence["rule_source"] == "test" for row in rows)


def test_direct_bans_keep_event_time_and_distinct_facts(db_session):
    when = datetime(2020, 1, 1)
    events = [store_event(db_session, source="test", plugin="test", event_type="security.ban",
                         ip="192.0.2.1", event_time=when + timedelta(seconds=i)) for i in range(2)]
    create_rule_based_insights(db_session, events[0])
    db_session.commit()
    rows = db_session.query(Insight).order_by(Insight.id).all()
    assert len(rows) == 2
    for row, event in zip(rows, events):
        assert row.occurred_at == event.event_time
        assert row.dedupe_key == f"security_ban_observed:event:{event.id}"
        assert row.evidence == {"version": 1, "kind": "direct", "event_count": 1,
                                "sample_count": 1, "sample_truncated": False}


def _record(db, **changes):
    from app.services.insights import record_insight

    values = dict(type="test.fact", title="Fact", description="Summary", level="high", confidence=0.9,
                  occurred_at=datetime(2020, 1, 1), correlation_key="ip:192.0.2.1",
                  dedupe_key="test.fact:event:1", related_event_ids=[1],
                  evidence={"version": 1, "kind": "direct", "event_count": 1,
                            "sample_count": 1, "sample_truncated": False})
    return record_insight(db, **{**values, **changes})


def test_same_key_and_legacy_identical_ids_do_not_notify_twice(db_session, monkeypatch):
    from app.services import insights

    calls = []
    monkeypatch.setattr(insights, "handle_insight", lambda *args: calls.append(args))
    first = _record(db_session)
    assert _record(db_session, related_event_ids=[2]) is first
    assert len(calls) == 1
    legacy = Insight(type="test.legacy", title="Legacy", related_event_ids=[3])
    db_session.add(legacy)
    db_session.flush()
    assert _record(db_session, type="test.legacy", related_event_ids=[3]) is legacy
    assert legacy.occurred_at is None and legacy.evidence is None
    assert len(calls) == 1


def test_competing_key_insert_preserves_outer_session(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.database.base import Base
    from app.models.settings import Setting

    engine = create_engine(f"sqlite:///{tmp_path / 'competing.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as losing, Session(engine) as winning:
        original_nested = losing.begin_nested
        winner_ids = []

        def insert_between_check_and_flush():
            winner = _record(winning)
            winning.commit()
            winner_ids.append(winner.id)
            return original_nested()

        monkeypatch.setattr(losing, "begin_nested", insert_between_check_and_flush)
        result = _record(losing)
        assert result.id == winner_ids[0]
        losing.add(Setting(key="test.outer", value="preserved"))
        losing.commit()
        assert losing.query(Insight).count() == 1
        assert losing.query(Setting).filter_by(key="test.outer").one().value == "preserved"
    engine.dispose()


@pytest.mark.parametrize("evidence", [
    {"version": True, "kind": "direct"}, {"version": 1, "kind": "unknown"},
    {"version": 1, "kind": "direct", "raw_data": "secret"},
    {"version": 1, "kind": "direct", "event_count": True},
    {"version": 1, "kind": "direct", "window_start": "2020-01-01T00:00:00"},
    {"version": 1, "kind": "direct", "rule_source": "é" * 4200},
])
def test_snapshot_rejects_unknown_fields_invalid_values_and_oversize(db_session, evidence):
    with pytest.raises(ValueError):
        _record(db_session, evidence=evidence)
    assert db_session.query(Insight).count() == 0


def test_sample_limit_is_enforced(db_session):
    with pytest.raises(ValueError, match="20 event IDs"):
        _record(db_session, related_event_ids=list(range(1, 22)))


def test_rule_snapshot_keeps_exact_counts_and_bounded_sample_after_retention(db_session):
    from app.models.events import Event

    start = datetime(2020, 1, 1)
    import_ruleset(db_session, {"schema_version": 1, "ruleset_version": "test", "rules": [
        {"id": "test.sample", "title": "Wave", "event_types": ["access.error"],
         "path_contains_any": ["/phase-two"], "threshold": 25, "group_by": "path"}
    ]}, source="test")
    for offset in range(25):
        _probe(db_session, start + timedelta(seconds=offset))
    row = db_session.query(Insight).filter_by(type="test.sample").one()
    assert row.correlation_key == "rule"
    assert row.evidence["event_count"] == 25
    assert row.evidence["sample_count"] == 20
    assert row.evidence["sample_truncated"] is True
    assert row.related_event_ids == list(range(6, 26))
    snapshot = dict(row.evidence)
    db_session.query(Event).delete()
    db_session.commit()
    db_session.refresh(row)
    assert row.evidence == snapshot


@pytest.mark.parametrize("failure", ["validation", "database"])
def test_failed_insight_rule_keeps_source_event_and_session(db_session, monkeypatch, failure):
    from app.models.settings import Setting
    from app.models.events import Event

    def fail(db, event):
        if failure == "database":
            db.add(Insight(type="broken", title=None))
            db.flush()
        raise ValueError("broken rule")

    monkeypatch.setattr("app.services.events.apply_declarative_insight_rules", fail)
    event = _probe(db_session, datetime(2020, 1, 1))
    db_session.add(Setting(key="test.outer", value="saved"))
    db_session.commit()
    assert db_session.query(Event).filter_by(id=event.id).one()
    assert db_session.query(Setting).filter_by(key="test.outer").one()
    assert db_session.query(Insight).count() == 0


def test_pair_uses_sorted_ids_and_latest_event_time(db_session):
    start = datetime(2020, 1, 1)
    access = _probe(db_session, start + timedelta(seconds=20))
    ban = store_event(db_session, source="test", plugin="test", event_type="security.ban",
                      ip="192.0.2.1", event_time=start + timedelta(seconds=30))
    row = db_session.query(Insight).filter_by(type="ban_after_access").one()
    assert row.occurred_at == ban.event_time
    assert row.related_event_ids == [access.id, ban.id]
    assert row.dedupe_key == f"ban_after_access:pair:{access.id}:{ban.id}"
    assert row.evidence["kind"] == "correlation"


def test_geoip_description_update_keeps_one_snapshot_and_notification(db_session, monkeypatch):
    calls = []
    monkeypatch.setattr("app.services.insights.handle_insight", lambda *args: calls.append(args))
    event = store_event(db_session, source="test", plugin="test", event_type="security.geoblock",
                        ip="192.0.2.1", event_time=datetime(2020, 1, 1))
    row = db_session.query(Insight).one()
    snapshot = dict(row.evidence)
    event.country = "DE"
    create_rule_based_insights(db_session, event)
    db_session.flush()
    assert db_session.query(Insight).count() == 1
    assert "from DE" in row.description
    assert row.evidence == snapshot
    assert len(calls) == 1


@pytest.mark.parametrize("fresh", [False, True])
def test_evidence_migration_preserves_legacy_ids_and_downgrades(tmp_path, monkeypatch, fresh):
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import MetaData, Table, create_engine, inspect, select
    from app.core import settings as settings_module

    url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setattr(settings_module.settings, "database_url", url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    engine = create_engine(url)
    if not fresh:
        command.upgrade(config, "e7f8a9b0c1d2")
        table = Table("insights", MetaData(), autoload_with=engine)
        with engine.begin() as connection:
            connection.execute(table.insert().values(id=42, timestamp=datetime(2020, 1, 1),
                type="legacy", title="Legacy", description="Old text", level="high", confidence=0.8))
    command.upgrade(config, "head")
    indexes = {index["name"] for index in inspect(engine).get_indexes("insights")}
    assert {"ix_insights_type_ip_timestamp", "ix_insights_type_correlation_occurred",
            "ix_insights_occurred_at"} <= indexes
    assert ["dedupe_key"] in [item["column_names"] for item in inspect(engine).get_unique_constraints("insights")]
    table = Table("insights", MetaData(), autoload_with=engine)
    if not fresh:
        with engine.connect() as connection:
            row = connection.execute(select(table)).one()
            assert row.id == 42 and row.description == "Old text"
            assert all(getattr(row, field) is None for field in ("occurred_at", "evidence", "dedupe_key", "correlation_key"))
    command.check(config)
    command.downgrade(config, "e7f8a9b0c1d2")
    assert "occurred_at" not in {column["name"] for column in inspect(engine).get_columns("insights")}
    table = Table("insights", MetaData(), autoload_with=engine)
    if not fresh:
        with engine.connect() as connection:
            assert connection.execute(select(table.c.id)).scalar_one() == 42
    engine.dispose()


def test_legacy_creation_time_does_not_block_a_different_event(db_session):
    from app.core.time import utc_now

    when = datetime(2020, 1, 1)
    import_ruleset(db_session, {"schema_version": 1, "ruleset_version": "test", "rules": [
        {"id": "test.legacy", "title": "Probe", "event_types": ["access.error"],
         "path_contains_any": ["/phase-two"]}
    ]}, source="test")
    legacy = Insight(type="test.legacy", title="Legacy", timestamp=utc_now(),
                     ip="192.0.2.1", related_event_ids=[999])
    db_session.add(legacy)
    db_session.flush()
    event = _probe(db_session, when)
    assert db_session.query(Insight).filter_by(type="test.legacy").count() == 2
    assert db_session.query(Insight).filter_by(related_event_ids=[event.id]).one().occurred_at == when
    assert legacy.evidence is None and legacy.occurred_at is None
