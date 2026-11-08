from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.core import settings as settings_module


@pytest.mark.parametrize("temporary_rows", [0, 1, 2])
def test_insight_migration_recovers_only_preserved_batch_rows(tmp_path: Path, monkeypatch, temporary_rows: int):
    database_url = f"sqlite:///{tmp_path / 'insights.db'}"
    monkeypatch.setattr(settings_module.settings, "database_url", database_url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "e7f8a9b0c1d2")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO insights (id, timestamp, type, title) "
            "VALUES (1, '2026-01-01', 'web.scan', 'Existing insight')"
        ))
        for definition in (
            "occurred_at DATETIME", "correlation_key VARCHAR(160)",
            "dedupe_key VARCHAR(255)", "evidence JSON",
        ):
            connection.execute(text(f"ALTER TABLE insights ADD COLUMN {definition}"))
        connection.execute(text("CREATE TABLE _alembic_tmp_insights AS SELECT * FROM insights WHERE 0"))
        if temporary_rows:
            connection.execute(text("INSERT INTO _alembic_tmp_insights SELECT * FROM insights"))
        if temporary_rows == 2:
            connection.execute(text("UPDATE _alembic_tmp_insights SET title = 'Unpreserved insight'"))

    if temporary_rows == 2:
        with pytest.raises(RuntimeError, match="unpreserved rows"):
            command.upgrade(config, "a8b9c0d1e2f3")
        assert inspect(engine).has_table("_alembic_tmp_insights")
    else:
        command.upgrade(config, "head")
        assert not inspect(engine).has_table("_alembic_tmp_insights")
        with engine.begin() as connection:
            assert connection.execute(text("SELECT title FROM insights WHERE id = 1")).scalar_one() == "Existing insight"
            connection.execute(text("UPDATE insights SET dedupe_key = 'unique' WHERE id = 1"))
            with pytest.raises(IntegrityError):
                connection.execute(text(
                    "INSERT INTO insights (timestamp, type, title, dedupe_key) "
                    "VALUES ('2026-01-01', 'web.scan', 'Duplicate', 'unique')"
                ))
    engine.dispose()
