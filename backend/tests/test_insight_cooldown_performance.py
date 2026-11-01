from datetime import datetime, timedelta
from pathlib import Path

import pytest

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects import sqlite

from app.core import settings as settings_module
from app.models.core import Insight


def test_insights_cooldown_index_migration_upgrade_and_downgrade(tmp_path: Path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'insights-cooldown-index.db'}"
    monkeypatch.setattr(settings_module.settings, "database_url", database_url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "e6f7a8b9c0d1")

    engine = create_engine(database_url)
    assert "ix_insights_type_ip_timestamp" not in {
        index["name"] for index in inspect(engine).get_indexes("insights")
    }

    command.upgrade(config, "head")
    indexes = {index["name"]: index["column_names"] for index in inspect(engine).get_indexes("insights")}
    assert indexes["ix_insights_type_ip_timestamp"] == ["type", "ip", "timestamp"]

    command.downgrade(config, "e6f7a8b9c0d1")
    assert "ix_insights_type_ip_timestamp" not in {
        index["name"] for index in inspect(engine).get_indexes("insights")
    }
    engine.dispose()


@pytest.mark.parametrize("event_time_cooldown", [False, True])
def test_cooldown_query_uses_composite_index_with_large_insights_table(db_session, event_time_cooldown):
    now = datetime(2026, 7, 17, 12, 0)
    db_session.add_all(
        [
            Insight(
                timestamp=now - timedelta(minutes=offset),
                type=f"unrelated-{offset % 100}",
                title="Unrelated insight",
                ip=f"192.0.2.{offset % 250 + 1}",
            )
            for offset in range(10_000)
        ]
    )
    db_session.add(
        Insight(
            timestamp=now - timedelta(minutes=1),
            occurred_at=now - timedelta(minutes=1),
            correlation_key="ip:198.51.100.10",
            type="web.scan",
            title="Matching insight",
            ip="198.51.100.10",
        )
    )
    db_session.commit()

    cooldown_query = (
        db_session.query(Insight)
        .filter(
            Insight.type == "web.scan",
            Insight.ip == "198.51.100.10",
            Insight.timestamp >= now - timedelta(minutes=5),
        )
        .limit(1)
    )
    if event_time_cooldown:
        cooldown_query = db_session.query(Insight).filter(
            Insight.type == "web.scan",
            Insight.correlation_key == "ip:198.51.100.10",
            Insight.occurred_at >= now - timedelta(minutes=5),
            Insight.occurred_at <= now,
        ).limit(1)
    compiled_query = cooldown_query.statement.compile(
        dialect=sqlite.dialect(),
        compile_kwargs={"literal_binds": True},
    )
    query_plan = db_session.execute(text(f"EXPLAIN QUERY PLAN {compiled_query}")).all()

    assert cooldown_query.first() is not None
    index_name = "ix_insights_type_correlation_occurred" if event_time_cooldown else "ix_insights_type_ip_timestamp"
    assert any(f"USING INDEX {index_name}" in row.detail for row in query_plan)
