from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app.core import settings as settings_module


PREVIOUS_HEAD = "b1c2d3e4f5a6"
ASN_BAN_REVISION = "b2c3d4e5f6a7"
REVISION = "c3d4e5f6a7b8"
TABLES = {
    "crowdsec_asn_bans",
    "crowdsec_asn_ban_exceptions",
    "crowdsec_asn_ban_enforcements",
}


def _config() -> Config:
    return Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))


def _revision(engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def test_fresh_database_upgrades_to_head_without_seeded_policies(tmp_path: Path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'asn-ban-fresh.db'}"
    monkeypatch.setattr(settings_module.settings, "database_url", database_url)
    config = _config()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    assert _revision(engine) == REVISION
    assert TABLES.issubset(inspect(engine).get_table_names())
    inspector = inspect(engine)
    assert "asn_organization" in {
        column["name"] for column in inspector.get_columns("events")
    }
    assert "asn_organization" in {
        column["name"] for column in inspector.get_columns("geoip_cache")
    }
    assert {
        "provider_candidate_name",
        "provider_candidate_first_ip",
        "provider_candidate_last_event_id",
        "provider_candidate_observations",
        "provider_candidate_distinct_ip_seen",
    }.issubset(
        {column["name"] for column in inspector.get_columns("crowdsec_asn_bans")}
    )
    with engine.connect() as connection:
        assert all(
            connection.scalar(text(f"SELECT COUNT(*) FROM {table_name}")) == 0
            for table_name in TABLES
        )
    command.check(config)
    engine.dispose()


def test_asn_organization_upgrade_preserves_existing_policy(tmp_path: Path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'asn-organization-upgrade.db'}"
    monkeypatch.setattr(settings_module.settings, "database_url", database_url)
    config = _config()
    command.upgrade(config, ASN_BAN_REVISION)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO crowdsec_asn_bans "
                "(id, asn, provider_name, provider_review_required, status, created_at, updated_at) "
                "VALUES (1, 'AS14618', 'Amazon.com, Inc.', 0, 'active', '2026-08-26', '2026-08-26')"
            )
        )

    command.upgrade(config, REVISION)

    assert _revision(engine) == REVISION
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT asn, provider_name, provider_candidate_observations, "
                "provider_candidate_distinct_ip_seen FROM crowdsec_asn_bans WHERE id = 1"
            )
        ).one()
    assert tuple(row) == ("AS14618", "Amazon.com, Inc.", 0, 0)
    command.check(config)
    engine.dispose()


def test_existing_database_upgrades_without_data_loss_or_policy_seeds(tmp_path: Path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'asn-ban-upgrade.db'}"
    monkeypatch.setattr(settings_module.settings, "database_url", database_url)
    config = _config()
    command.upgrade(config, PREVIOUS_HEAD)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO settings (key, value) VALUES ('test.phase1', 'kept')"))

    command.upgrade(config, REVISION)

    assert _revision(engine) == REVISION
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT value FROM settings WHERE key = 'test.phase1'")) == "kept"
        assert all(
            connection.scalar(text(f"SELECT COUNT(*) FROM {table_name}")) == 0
            for table_name in TABLES
        )
    command.check(config)
    engine.dispose()


def test_revision_downgrades_in_dependency_order(tmp_path: Path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'asn-ban-downgrade.db'}"
    monkeypatch.setattr(settings_module.settings, "database_url", database_url)
    config = _config()
    command.upgrade(config, REVISION)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO crowdsec_asn_bans "
                "(id, asn, provider_review_required, status, created_at, updated_at) "
                "VALUES (1, 'AS64505', 0, 'active', '2026-08-25', '2026-08-25')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO crowdsec_asn_ban_exceptions (asn_ban_id, ip, created_at) "
                "VALUES (1, '8.8.4.4', '2026-08-25')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO crowdsec_asn_ban_enforcements "
                "(asn_ban_id, ip, release_pending, created_at, updated_at) "
                "VALUES (1, '8.8.4.4', 0, '2026-08-25', '2026-08-25')"
            )
        )

    command.downgrade(config, PREVIOUS_HEAD)

    assert _revision(engine) == PREVIOUS_HEAD
    assert TABLES.isdisjoint(inspect(engine).get_table_names())
    engine.dispose()
