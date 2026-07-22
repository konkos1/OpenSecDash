from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app.core import settings as settings_module
from app.database.base import Base


PREVIOUS_HEAD = "f1a2b3c4d5e6"
UNIQUE_INDEXES = {
    "api_tokens": "ix_api_tokens_token_hash",
    "instance_files": "ix_instance_files_kind",
    "user_preferences": "ix_user_preferences_user_id",
    "user_sessions": "ix_user_sessions_token_hash",
    "users": "ix_users_username",
}
SOURCE_CONSTRAINTS = {
    "assets": "uq_asset_source_external",
    "systems": "uq_system_source_external",
}


def _config() -> Config:
    return Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))


def _index_is_unique(inspector, table_name: str, index_name: str) -> bool:
    index = next(
        item
        for item in inspector.get_indexes(table_name)
        if item["name"] == index_name
    )
    return bool(index["unique"])


def test_migration_normalizes_unique_indexes_and_source_constraints(tmp_path: Path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'unique-constraints.db'}"
    monkeypatch.setattr(settings_module.settings, "database_url", database_url)
    config = _config()
    command.upgrade(config, PREVIOUS_HEAD)

    engine = create_engine(database_url)
    inspector = inspect(engine)
    assert all(
        not _index_is_unique(inspector, table_name, index_name)
        for table_name, index_name in UNIQUE_INDEXES.items()
    )
    assert all(
        constraint_name not in {item["name"] for item in inspector.get_unique_constraints(table_name)}
        for table_name, constraint_name in SOURCE_CONSTRAINTS.items()
    )

    command.upgrade(config, "head")

    inspector = inspect(engine)
    assert all(
        _index_is_unique(inspector, table_name, index_name)
        for table_name, index_name in UNIQUE_INDEXES.items()
    )
    assert all(
        constraint_name in {item["name"] for item in inspector.get_unique_constraints(table_name)}
        for table_name, constraint_name in SOURCE_CONSTRAINTS.items()
    )
    command.check(config)

    command.downgrade(config, PREVIOUS_HEAD)
    inspector = inspect(engine)
    assert all(
        not _index_is_unique(inspector, table_name, index_name)
        for table_name, index_name in UNIQUE_INDEXES.items()
    )
    assert all(
        constraint_name not in {item["name"] for item in inspector.get_unique_constraints(table_name)}
        for table_name, constraint_name in SOURCE_CONSTRAINTS.items()
    )
    engine.dispose()


def test_migration_rejects_duplicate_source_ids_before_schema_changes(tmp_path: Path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'duplicate-source-ids.db'}"
    monkeypatch.setattr(settings_module.settings, "database_url", database_url)
    config = _config()
    command.upgrade(config, PREVIOUS_HEAD)

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO systems (vmid, hostname, system_type, source_plugin, external_id) "
                "VALUES (:vmid, :hostname, :system_type, :source_plugin, :external_id)"
            ),
            [
                {
                    "vmid": "one",
                    "hostname": "one",
                    "system_type": "custom",
                    "source_plugin": "test",
                    "external_id": "same",
                },
                {
                    "vmid": "two",
                    "hostname": "two",
                    "system_type": "custom",
                    "source_plugin": "test",
                    "external_id": "same",
                },
            ],
        )
        connection.execute(
            text(
                "INSERT INTO systems (vmid, hostname, system_type, source_plugin, external_id) "
                "VALUES ('asset-parent', 'asset-parent', 'custom', 'test', 'asset-parent')"
            )
        )
        system_id = connection.scalar(text("SELECT id FROM systems WHERE vmid = 'asset-parent'"))
        connection.execute(
            text(
                "INSERT INTO assets "
                "(system_id, name, version, update_available, source_plugin, external_id) "
                "VALUES (:system_id, :name, '', 0, 'test', 'same')"
            ),
            [
                {"system_id": system_id, "name": "one"},
                {"system_id": system_id, "name": "two"},
            ],
        )

    with pytest.raises(
        RuntimeError,
        match=r"assets: 1 duplicate group\(s\); systems: 1 duplicate group\(s\)",
    ):
        command.upgrade(config, "head")

    with engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_revision() == PREVIOUS_HEAD
    inspector = inspect(engine)
    assert all(
        not _index_is_unique(inspector, table_name, index_name)
        for table_name, index_name in UNIQUE_INDEXES.items()
    )
    engine.dispose()


def test_migration_accepts_schema_already_created_from_metadata(tmp_path: Path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'metadata-schema.db'}"
    monkeypatch.setattr(settings_module.settings, "database_url", database_url)
    config = _config()
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    command.stamp(config, PREVIOUS_HEAD)

    command.upgrade(config, "head")
    command.check(config)

    engine.dispose()
