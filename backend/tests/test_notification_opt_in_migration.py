from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app.core import settings as settings_module


PREVIOUS_REVISION = "c3d4e5f6a7b8"
REVISION = "d5e6f7a8b9c0"
HEAD_REVISION = "e7f8a9b0c1d2"


def _config() -> Config:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes["configure_logger"] = False
    return config


def _revision(engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def _insert_rule(connection, rule_id: str, enabled: int | None = None) -> None:
    parameters = {"rule_id": rule_id, "enabled": enabled}
    if enabled is None:
        connection.execute(
            text(
                "INSERT INTO notification_rules "
                "(rule_id, name, match_types, countries, created_at, updated_at) "
                "VALUES (:rule_id, 'Test rule', '[]', '[]', '2026-08-26', '2026-08-26')"
            ),
            parameters,
        )
        return
    connection.execute(
        text(
            "INSERT INTO notification_rules "
            "(rule_id, name, match_types, countries, created_at, updated_at, enabled) "
            "VALUES (:rule_id, 'Test rule', '[]', '[]', '2026-08-26', '2026-08-26', :enabled)"
        ),
        parameters,
    )


def test_notification_default_changes_without_overwriting_saved_choices(tmp_path: Path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'notification-opt-in.db'}"
    monkeypatch.setattr(settings_module.settings, "database_url", database_url)
    config = _config()
    command.upgrade(config, PREVIOUS_REVISION)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        _insert_rule(connection, "test.enabled", enabled=1)
        _insert_rule(connection, "test.disabled", enabled=0)

    command.upgrade(config, REVISION)

    assert _revision(engine) == REVISION
    with engine.begin() as connection:
        saved = connection.execute(
            text("SELECT rule_id, enabled FROM notification_rules ORDER BY rule_id")
        ).all()
        _insert_rule(connection, "test.default")
    assert [tuple(row) for row in saved] == [("test.disabled", 0), ("test.enabled", 1)]
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT enabled FROM notification_rules WHERE rule_id = 'test.default'")
        ) == 0
    enabled_column = next(
        column for column in inspect(engine).get_columns("notification_rules")
        if column["name"] == "enabled"
    )
    assert enabled_column["default"] in {"0", "(0)"}
    command.upgrade(config, "head")
    assert _revision(engine) == HEAD_REVISION
    command.check(config)
    engine.dispose()
