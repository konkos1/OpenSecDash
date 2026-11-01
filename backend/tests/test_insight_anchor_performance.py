from datetime import datetime, timedelta
from pathlib import Path
import sqlite3

import pytest

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, insert, inspect

from app.core import settings as settings_module
from app.models.events import Event
from app.services.insight_correlation import run_level2_tick


@pytest.mark.parametrize("historical_bans", [1_000, 100_000])
@pytest.mark.parametrize("current_bans", [0, 3])
@pytest.mark.parametrize("analyzed", [False, True])
def test_anchor_scan_is_bounded_by_creation_window(db_session, historical_bans, current_bans, analyzed):
    now = datetime(2026, 11, 1, 12)
    for offset in range(0, historical_bans, 2_000):
        db_session.execute(insert(Event), [dict(
            id=index + 1, event_type="security.ban", ip="192.0.2.1",
            created_at=now - timedelta(days=30), event_time=now,
        ) for index in range(offset, min(offset + 2_000, historical_bans))])
    for index in range(current_bans):
        # Creation order need not match ID order; imported event times may be historical.
        db_session.add(Event(id=historical_bans + index + 1, event_type="security.ban", ip="192.0.2.2",
            created_at=now - timedelta(minutes=index), event_time=now - timedelta(days=60)))
    db_session.add(Event(event_type="security.ban", ip="192.0.2.3",
        created_at=now + timedelta(seconds=1), event_time=now))
    db_session.commit()
    if analyzed:
        db_session.connection().exec_driver_sql("ANALYZE")

    queries = []
    bind = db_session.get_bind()

    def capture(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT") and "FROM events" in statement:
            queries.append((statement, parameters))

    event.listen(bind, "before_cursor_execute", capture)
    try:
        result = run_level2_tick(db_session, now=now)
    finally:
        event.remove(bind, "before_cursor_execute", capture)
    assert result == {"processed": current_bans, "errors": 0, "completed": True}
    statement, parameters = queries[0]
    connection = db_session.connection()
    raw = connection.connection.dbapi_connection
    assert isinstance(raw, sqlite3.Connection)
    steps = 0

    def progress():
        nonlocal steps
        steps += 1
        return 0

    raw.set_progress_handler(progress, 1)
    try:
        rows = connection.exec_driver_sql(statement, parameters).all()
    finally:
        raw.set_progress_handler(None, 0)
    assert [row._mapping["events_id"] for row in rows] == list(range(historical_bans + 1, historical_bans + current_bans + 1))
    # A time-bounded miss costs tens of steps, regardless of retained ban history.
    assert steps < 1_000, steps
    plan = connection.exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters).all()
    assert any("ix_events_type_created" in row[-1] and "created_at>?" in row[-1]
        and "created_at<?" in row[-1] for row in plan)


def test_anchor_index_migration_upgrade_and_downgrade(tmp_path: Path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'anchor-index.db'}"
    monkeypatch.setattr(settings_module.settings, "database_url", database_url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "c0d1e2f3a4b5")
    engine = create_engine(database_url)
    try:
        assert "ix_events_type_created" not in {index["name"] for index in inspect(engine).get_indexes("events")}
        command.upgrade(config, "head")
        indexes = {index["name"]: index["column_names"] for index in inspect(engine).get_indexes("events")}
        assert indexes["ix_events_type_created"] == ["event_type", "created_at"]
        command.downgrade(config, "c0d1e2f3a4b5")
        assert "ix_events_type_created" not in {index["name"] for index in inspect(engine).get_indexes("events")}
    finally:
        engine.dispose()
