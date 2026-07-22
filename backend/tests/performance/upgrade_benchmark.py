"""Exercise the complete migration and startup path on synthetic legacy data."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time

from alembic import command
from sqlalchemy import create_engine, event, func
from sqlalchemy.orm import sessionmaker

# The benchmark must never create a local key file. This value is test-only and
# lives solely in the temporary process/database used by the release gate.
os.environ.setdefault("OSD_SECRET_KEY", "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")

from app.core.settings import settings
from app.database import init_db as init_db_module
from app.database import session as session_module
from app.database.migrations import alembic_config
from app.models.events import Event
from app.models.settings import Setting


LEGACY_REVISION = "7f4a4a9c2b1e"
DEFAULT_EVENT_COUNT = 10_000


def _insert_legacy_rows(database_path: Path, event_count: int) -> None:
    end_time = datetime(2026, 7, 22, 12)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE settings SET value = ? WHERE key = ?",
            ("synthetic-legacy-password", "mqtt_password"),
        )
        connection.executemany(
            """
            INSERT INTO events
                (timestamp, source, plugin, event_type, ip, country, hostname,
                 status_code, path, severity, data_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    (end_time - timedelta(seconds=index)).isoformat(sep=" "),
                    "legacy",
                    "traefik_log",
                    "access.allowed",
                    f"198.51.100.{index % 250 + 1}",
                    "DE",
                    "proxy.example.test",
                    200,
                    f"/legacy/{index}",
                    "info",
                    json.dumps({"legacy_id": index}),
                )
                for index in range(event_count)
            ),
        )
        connection.commit()


def run(event_count: int) -> dict[str, object]:
    original_database_url = settings.database_url
    original_engine = init_db_module.engine
    original_session_factory = session_module.SessionLocal
    try:
        with tempfile.TemporaryDirectory(prefix="opensecdash-upgrade-benchmark-") as directory:
            database_path = Path(directory) / "legacy.db"
            settings.database_url = f"sqlite:///{database_path}"
            config = alembic_config()
            command.upgrade(config, LEGACY_REVISION)
            _insert_legacy_rows(database_path, event_count)

            migration_started = time.perf_counter()
            command.upgrade(config, "head")
            migration_ms = (time.perf_counter() - migration_started) * 1_000

            engine = create_engine(
                settings.database_url,
                connect_args={"check_same_thread": False, "timeout": 10},
            )
            session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
            init_db_module.engine = engine
            session_module.SessionLocal = session_factory

            first_started = time.perf_counter()
            init_db_module.init_db()
            first_start_ms = (time.perf_counter() - first_started) * 1_000

            statements: list[str] = []

            def capture_statement(connection, cursor, statement, parameters, context, executemany) -> None:
                statements.append(" ".join(statement.split()).lower())

            event.listen(engine, "before_cursor_execute", capture_statement)
            try:
                second_started = time.perf_counter()
                init_db_module.init_db()
                second_start_ms = (time.perf_counter() - second_started) * 1_000
            finally:
                event.remove(engine, "before_cursor_execute", capture_statement)

            db = session_factory()
            try:
                events_after = db.query(func.count(Event.id)).scalar() or 0
                auth_enabled = db.query(Setting).filter(Setting.key == "auth.enabled").first()
                password = db.query(Setting).filter(Setting.key == "mqtt_password").one().value
                marker = (
                    db.query(Setting)
                    .filter(Setting.key == init_db_module.EVENT_DEDUPE_MAINTENANCE_KEY)
                    .one()
                    .value
                )
            finally:
                db.close()
                engine.dispose()

            gates = {
                "all_legacy_events_preserved": events_after == event_count,
                "auth_remains_disabled": auth_enabled is None or auth_enabled.value != "true",
                "legacy_secret_encrypted": password.startswith("enc:v1:"),
                "dedupe_marker_written": marker == init_db_module.EVENT_DEDUPE_MAINTENANCE_VERSION,
                "second_start_skips_event_scan": not any(" from events" in statement for statement in statements),
            }
            return {
                "profile": "upgrade",
                "legacy_revision": LEGACY_REVISION,
                "events": event_count,
                "database_mib": round(database_path.stat().st_size / (1024 * 1024), 2),
                "migration_ms": round(migration_ms, 2),
                "first_start_ms": round(first_start_ms, 2),
                "second_start_ms": round(second_start_ms, 2),
                "gates": gates,
                "passed": all(gates.values()),
            }
    finally:
        settings.database_url = original_database_url
        init_db_module.engine = original_engine
        session_module.SessionLocal = original_session_factory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=DEFAULT_EVENT_COUNT)
    parser.add_argument("--enforce-gates", action="store_true")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if arguments.events < 1:
        parser.error("--events must be positive")
    report = run(arguments.events)
    serialized = json.dumps(report, indent=2)
    if arguments.output is None:
        print(serialized)
    else:
        arguments.output.write_text(serialized + "\n", encoding="utf-8")
    if arguments.enforce_gates and not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
