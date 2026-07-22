from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.core.settings import settings
from app.core.time import utc_now
from app.models.core import Diagnostic


BACKEND_DIR = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"
logger = logging.getLogger(__name__)


def alembic_config() -> Config:
    """Build Alembic config from runtime settings.

    Tests and deployments can override ``DATABASE_URL`` without editing
    ``alembic.ini``; keep all migration entry points using this helper.
    """
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.attributes["configure_logger"] = False
    return config


def migration_status() -> dict[str, str | bool | None]:
    """Return a diagnostics-friendly schema status snapshot."""
    config = alembic_config()
    script = ScriptDirectory.from_config(config)
    head = script.get_current_head()
    connect_args = {"check_same_thread": False, "timeout": 10} if settings.database_url.startswith("sqlite") else {}
    engine = create_engine(settings.database_url, connect_args=connect_args)
    with engine.connect() as connection:
        if settings.database_url.startswith("sqlite"):
            connection.execute(text("PRAGMA busy_timeout = 10000"))
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


def run_auto_migrations_if_enabled() -> dict[str, str | bool | None]:
    """Run startup migrations and log enough context for operators.

    The return value is intentionally plain data so ``main.py`` can log it again
    after DB-backed logging has been configured.
    """
    before = migration_status()
    if not settings.auto_migrate:
        logger.info("Database migration: auto-migration disabled: current=%s head=%s", before["current"], before["head"])
        return {**before, "auto_migrate": False, "applied": False}

    if before["up_to_date"]:
        logger.info("Database migration: schema already up to date: current=%s", before["current"])
        return {**before, "auto_migrate": True, "applied": False}

    logger.info("Database migration: auto-migration starting: current=%s head=%s", before["current"], before["head"])
    try:
        command.upgrade(alembic_config(), "head")
    except Exception:
        logger.exception(
            "Database migration: auto-migration failed: current=%s head=%s. "
            "If this is SQLite, stop other OpenSecDash/DB tools and retry.",
            before["current"],
            before["head"],
        )
        raise
    after = migration_status()
    logger.info("Database migration: auto-migration finished: current=%s head=%s", after["current"], after["head"])
    return {**after, "auto_migrate": True, "applied": True, "previous": before["current"]}


def update_migration_diagnostic(db: Session) -> None:
    """Mirror Alembic status into the Diagnostics page."""
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
