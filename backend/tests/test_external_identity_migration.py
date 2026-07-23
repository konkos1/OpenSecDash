import importlib.util
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.core import settings as settings_module
from app.database.base import Base
from app.services.auth import hash_password, verify_password


def _load_revision_module(filename: str):
    path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DISABLED_PASSWORD_MARKER = _load_revision_module("b1c2d3e4f5a6_add_external_identities.py").DISABLED_PASSWORD_MARKER
PREVIOUS_HEAD = "a3b4c5d6e7f8"
REVISION = "b1c2d3e4f5a6"
ISSUER = "https://idp.example/realms/homelab"
OTHER_ISSUER = "https://other-idp.example"


def _config() -> Config:
    return Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))


def _columns(inspector, table_name: str) -> dict[str, dict]:
    return {column["name"]: column for column in inspector.get_columns(table_name)}


def _seed_pre_oidc_database(engine) -> str:
    local_hash = hash_password("password123")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, username, password_hash, role, is_active, created_at, updated_at) "
                "VALUES (:id, :username, :password_hash, :role, 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            ),
            [
                {"id": 1, "username": "admin", "password_hash": local_hash, "role": "admin"},
                {"id": 2, "username": "viewer", "password_hash": local_hash, "role": "viewer"},
            ],
        )
        connection.execute(
            text(
                "INSERT INTO user_preferences (user_id, language, live_default, theme, accent_color, live_page_refresh) "
                "VALUES (1, 'de', 'true', 'dark', 'blue', 'true')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO user_sessions (token_hash, user_id, created_at, expires_at) "
                "VALUES (:token_hash, :user_id, '2026-01-01 00:00:00', '2027-01-01 00:00:00')"
            ),
            [
                {"token_hash": "a" * 64, "user_id": 1},
                {"token_hash": "b" * 64, "user_id": 2},
            ],
        )
    return local_hash


def _insert_identity(engine, user_id: int, issuer: str, subject: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO external_identities (user_id, provider, issuer, subject, created_at) "
                "VALUES (:user_id, 'oidc', :issuer, :subject, '2026-01-01 00:00:00')"
            ),
            {"user_id": user_id, "issuer": issuer, "subject": subject},
        )


def test_migration_preserves_existing_users_and_classifies_sessions_as_password(tmp_path: Path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'external-identities.db'}"
    monkeypatch.setattr(settings_module.settings, "database_url", database_url)
    config = _config()
    command.upgrade(config, PREVIOUS_HEAD)
    engine = create_engine(database_url)
    local_hash = _seed_pre_oidc_database(engine)

    inspector = inspect(engine)
    assert "external_identities" not in inspector.get_table_names()
    assert "auth_method" not in _columns(inspector, "user_sessions")
    assert _columns(inspector, "users")["password_hash"]["nullable"] is False

    command.upgrade(config, REVISION)
    command.check(config)

    inspector = inspect(engine)
    assert "external_identities" in inspector.get_table_names()
    assert _columns(inspector, "user_sessions")["auth_method"]["nullable"] is False
    assert _columns(inspector, "users")["password_hash"]["nullable"] is True
    with engine.connect() as connection:
        assert connection.execute(text("SELECT id, username, role, password_hash FROM users ORDER BY id")).all() == [
            (1, "admin", "admin", local_hash),
            (2, "viewer", "viewer", local_hash),
        ]
        assert connection.execute(
            text("SELECT user_id, language, theme FROM user_preferences")
        ).all() == [(1, "de", "dark")]
        assert connection.execute(
            text("SELECT user_id, auth_method FROM user_sessions ORDER BY user_id")
        ).all() == [(1, "password"), (2, "password")]
    engine.dispose()


def test_migration_enforces_external_identity_uniqueness(tmp_path: Path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'identity-constraints.db'}"
    monkeypatch.setattr(settings_module.settings, "database_url", database_url)
    config = _config()
    command.upgrade(config, PREVIOUS_HEAD)
    engine = create_engine(database_url)
    _seed_pre_oidc_database(engine)
    command.upgrade(config, REVISION)

    inspector = inspect(engine)
    # SQLite unique-constraint reflection only reports constraints once the
    # inspector has read the table list, so warm it before asking.
    inspector.get_table_names()
    constraints = {item["name"]: item["column_names"] for item in inspector.get_unique_constraints("external_identities")}
    assert constraints["uq_external_identity_provider_subject"] == ["provider", "issuer", "subject"]
    assert constraints["uq_external_identity_provider_user"] == ["provider", "user_id"]
    assert "ix_external_identities_user_id" in {item["name"] for item in inspector.get_indexes("external_identities")}

    _insert_identity(engine, user_id=1, issuer=ISSUER, subject="subject-1")
    with pytest.raises(IntegrityError):
        _insert_identity(engine, user_id=2, issuer=ISSUER, subject="subject-1")
    with pytest.raises(IntegrityError):
        _insert_identity(engine, user_id=1, issuer=ISSUER, subject="subject-2")
    _insert_identity(engine, user_id=2, issuer=OTHER_ISSUER, subject="subject-1")
    engine.dispose()


def test_downgrade_keeps_passwordless_users_locked_out_and_upgrade_repeats(tmp_path: Path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'identity-downgrade.db'}"
    monkeypatch.setattr(settings_module.settings, "database_url", database_url)
    config = _config()
    command.upgrade(config, PREVIOUS_HEAD)
    engine = create_engine(database_url)
    local_hash = _seed_pre_oidc_database(engine)
    command.upgrade(config, REVISION)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, username, password_hash, role, is_active, created_at, updated_at) "
                "VALUES (3, 'oidc-only', NULL, 'viewer', 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO external_identities (user_id, provider, issuer, subject, created_at) "
                "VALUES (3, 'oidc', :issuer, 'subject-1', '2026-01-01 00:00:00')"
            ),
            {"issuer": ISSUER},
        )

    command.downgrade(config, PREVIOUS_HEAD)

    inspector = inspect(engine)
    assert "external_identities" not in inspector.get_table_names()
    assert "auth_method" not in _columns(inspector, "user_sessions")
    assert _columns(inspector, "users")["password_hash"]["nullable"] is False
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT id, username, password_hash FROM users ORDER BY id")).all()
    assert [(row[0], row[1]) for row in rows] == [(1, "admin"), (2, "viewer"), (3, "oidc-only")]
    assert rows[0][2] == local_hash
    assert rows[2][2] == DISABLED_PASSWORD_MARKER
    assert verify_password(DISABLED_PASSWORD_MARKER, DISABLED_PASSWORD_MARKER) is False
    assert verify_password("password123", DISABLED_PASSWORD_MARKER) is False

    command.upgrade(config, REVISION)
    command.check(config)
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT auth_method FROM user_sessions ORDER BY user_id")
        ).all() == [("password",), ("password",)]
    engine.dispose()


def test_migration_accepts_schema_already_created_from_metadata(tmp_path: Path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'identity-metadata-schema.db'}"
    monkeypatch.setattr(settings_module.settings, "database_url", database_url)
    config = _config()
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    command.stamp(config, PREVIOUS_HEAD)

    command.upgrade(config, REVISION)
    command.check(config)

    engine.dispose()
