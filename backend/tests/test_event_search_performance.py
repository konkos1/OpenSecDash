from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core import settings as settings_module
from tests.performance.event_search_benchmark import DEFAULT_ITERATIONS, _timing_summary


PREVIOUS_REVISION = "d5e6f7a8b9c0"


def _config() -> Config:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes["configure_logger"] = False
    return config


def _indexes(engine) -> dict[str, list[str]]:
    return {
        index["name"]: index["column_names"]
        for index in inspect(engine).get_indexes("events")
        if index["name"] is not None
    }


def test_status_time_index_migration_replaces_single_status_index(tmp_path: Path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'event-search-index.db'}"
    monkeypatch.setattr(settings_module.settings, "database_url", database_url)
    config = _config()
    command.upgrade(config, PREVIOUS_REVISION)
    engine = create_engine(database_url)

    previous_indexes = _indexes(engine)
    assert previous_indexes["ix_events_status_code"] == ["status_code"]
    assert "ix_events_status_time" not in previous_indexes
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO events "
                "(timestamp, event_time, source, plugin, event_type, severity, status_code) "
                "VALUES ('2026-08-27', '2026-08-27', 'test', 'test', 'access.error', "
                "'warning', 404)"
            )
        )

    command.upgrade(config, "head")

    upgraded_indexes = _indexes(engine)
    assert upgraded_indexes["ix_events_status_time"] == ["status_code", "event_time"]
    assert "ix_events_status_code" not in upgraded_indexes
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM events WHERE status_code = 404")) == 1
    command.check(config)

    command.downgrade(config, PREVIOUS_REVISION)

    downgraded_indexes = _indexes(engine)
    assert downgraded_indexes["ix_events_status_code"] == ["status_code"]
    assert "ix_events_status_time" not in downgraded_indexes
    engine.dispose()


def test_release_search_benchmark_uses_meaningful_p95_sample_count():
    samples = [float(value) for value in range(1, DEFAULT_ITERATIONS + 1)]

    summary = _timing_summary(samples)

    assert DEFAULT_ITERATIONS == 20
    assert summary["samples_ms"] == samples
    assert summary["p95_ms"] == 19.05
