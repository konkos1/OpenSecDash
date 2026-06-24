from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.database.base import Base
from app.database.session import engine
from app.models import *  # noqa: F401,F403 - import models for metadata registration
from app.models.core import Diagnostic, PluginRecord
from app.models.settings import Setting


DEFAULT_SETTINGS = {
    "language": "en",
    "domain": "",
    "live_default": "true",
    "retention_days": "30",
    "theme": "auto",
    "timezone": "auto",
    "asset_source_type": "file",
    "asset_source": "dev-data/apps-installed.json",
    "github_token": "",
    "action_dry_run": "true",
    "apps_master": "opensecdash",
    "log_file_enabled": "true",
    "log_file_path": "logs/opensecdash.log",
    "log_level": "INFO",
    "mqtt_enabled": "false",
    "mqtt_host": "",
    "mqtt_port": "1883",
    "mqtt_username": "",
    "mqtt_password": "",
    "mqtt_topic_prefix": "opensecdash",
}

CORE_PLUGINS = [
    ("geoip", "GeoIP / ASN", ["enrichment"]),
]


def _sqlite_columns(table_name: str) -> set[str]:
    inspector = inspect(engine)
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _add_column(table: str, column_sql: str) -> None:
    column_name = column_sql.split()[0]
    if column_name in _sqlite_columns(table):
        return
    with engine.begin() as connection:
        connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column_sql}"))


def _migrate_legacy_sqlite() -> None:
    # create_all does not alter already existing prototype tables. Keep the old
    # database usable by adding the v1 columns that are required by the app.
    for column in [
        "created_at DATETIME",
        "event_time DATETIME",
        "source_id VARCHAR(100)",
        "plugin_id VARCHAR(100)",
        "asn VARCHAR(32)",
        "asset_id INTEGER",
        "method VARCHAR(16)",
        "raw_data TEXT",
        "retention_class VARCHAR(20) DEFAULT 'raw'",
    ]:
        _add_column("events", column)

    for column in [
        "type VARCHAR(50) DEFAULT 'application'",
        "description TEXT",
        "enabled BOOLEAN DEFAULT 1",
        "hostname VARCHAR(255)",
        "url VARCHAR(2048)",
        "host_url VARCHAR(2048)",
        "icon VARCHAR(255)",
        "tags JSON",
        "release_api_url VARCHAR(2048)",
        "release_web_url VARCHAR(2048)",
        "update_check_type VARCHAR(50) DEFAULT 'github_release'",
        "mqtt_publish_enabled BOOLEAN DEFAULT 0",
        "last_checked DATETIME",
    ]:
        _add_column("assets", column)

    _add_column("systems", "last_seen DATETIME")


def seed_defaults(db: Session) -> None:
    # ASN enrichment is implemented by the bundled GeoIP plugin. Remove the old
    # placeholder core record if an earlier development database contains it.
    db.query(Diagnostic).filter(Diagnostic.plugin == "asn").delete()
    db.query(PluginRecord).filter(PluginRecord.id == "asn").delete()

    for key, value in DEFAULT_SETTINGS.items():
        if db.query(Setting).filter(Setting.key == key).first() is None:
            db.add(Setting(key=key, value=value))

    for plugin_id, name, capabilities in CORE_PLUGINS:
        plugin = db.query(PluginRecord).filter(PluginRecord.id == plugin_id).first()
        if plugin is None:
            db.add(
                PluginRecord(
                    id=plugin_id,
                    name=name,
                    version="1.0.0",
                    capabilities=capabilities,
                    status="healthy" if plugin_id != "mqtt" else "disabled",
                )
            )
        diagnostic = db.query(Diagnostic).filter(Diagnostic.plugin == plugin_id).first()
        if diagnostic is None:
            db.add(
                Diagnostic(
                    plugin=plugin_id,
                    component="plugin",
                    status="healthy" if plugin_id != "mqtt" else "disabled",
                )
            )
    db.commit()


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _migrate_legacy_sqlite()
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        seed_defaults(db)
        from app.services.events import cleanup_duplicate_events

        deleted = cleanup_duplicate_events(db)
        if deleted:
            db.commit()
    finally:
        db.close()
