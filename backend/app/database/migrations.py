from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from app.core.settings import settings
from app.core.time import utc_now
from app.models.core import Diagnostic


BACKEND_DIR = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"


def alembic_config() -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    return config


def migration_status() -> dict[str, str | bool | None]:
    config = alembic_config()
    script = ScriptDirectory.from_config(config)
    head = script.get_current_head()
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    with engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        context = MigrationContext.configure(connection)
        current = context.get_current_revision()

    if current == head:
        status = "healthy"
        message = f"Database schema is up to date ({current})."
    elif current is None and tables:
        status = "warning"
        message = f"Database has tables but no Alembic version; upgrade will adopt/migrate it to {head}."
    elif current is None:
        status = "warning"
        message = f"Database is not initialized; upgrade will create schema {head}."
    else:
        status = "warning"
        message = f"Database schema is at {current}; latest is {head}."

    return {"status": status, "message": message, "current": current, "head": head, "up_to_date": current == head}


def run_auto_migrations_if_enabled() -> None:
    if not settings.auto_migrate:
        return
    command.upgrade(alembic_config(), "head")


def update_migration_diagnostic(db: Session) -> None:
    try:
        status = migration_status()
        diagnostic_status = str(status["status"])
        message = str(status["message"])
    except Exception as exc:
        diagnostic_status = "error"
        message = f"Could not determine database migration status: {exc}"

    row = db.query(Diagnostic).filter(Diagnostic.plugin == "system", Diagnostic.component == "database_migrations").first()
    if row is None:
        row = Diagnostic(plugin="system", component="database_migrations")
        db.add(row)
    row.status = diagnostic_status
    row.last_run = utc_now()
    row.last_error = None if diagnostic_status == "healthy" else message
    if diagnostic_status == "healthy":
        row.last_error = message
    db.commit()
