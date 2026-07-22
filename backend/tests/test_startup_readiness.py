import io
import logging
from datetime import datetime

import pytest
from alembic import command
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app import main as main_module
from app.core import logging as logging_module
from app.core.settings import settings
from app.database import init_db as init_db_module
from app.database import session as session_module
from app.database.base import Base
from app.database.migrations import alembic_config
from app.models.events import Event
from app.models.settings import Setting
from app.services import events as events_module


def test_health_does_not_access_the_database(monkeypatch):
    class UnavailableEngine:
        def connect(self):
            raise AssertionError("health must not open a database connection")

    monkeypatch.setattr(main_module, "engine", UnavailableEngine(), raising=False)

    response = TestClient(main_module.app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_only_executes_select_one_and_does_not_run_startup_maintenance(tmp_path, monkeypatch):
    calls = {"init_db": 0, "seed_defaults": 0, "encrypt_secrets": 0, "dedupe": 0}

    def record_call(name):
        def recorder(*args, **kwargs):
            calls[name] += 1

        return recorder

    engine = create_engine(f"sqlite:///{tmp_path / 'probe.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db_session = session_factory()
    monkeypatch.setattr(main_module, "engine", engine, raising=False)
    monkeypatch.setattr(main_module, "init_db", record_call("init_db"))
    monkeypatch.setattr(init_db_module, "seed_defaults", record_call("seed_defaults"))
    monkeypatch.setattr(init_db_module, "encrypt_legacy_plaintext_secrets", record_call("encrypt_secrets"))
    monkeypatch.setattr(events_module, "cleanup_duplicate_events", record_call("dedupe"))

    db_session.add_all(
        [
            Setting(key="probe-test", value="unchanged"),
            Event(
                timestamp=datetime(2026, 7, 22, 12, 0, 0),
                event_time=datetime(2026, 7, 22, 12, 0, 0),
                source="test",
                plugin="test",
                event_type="probe.test",
            ),
        ]
    )
    db_session.commit()
    settings_before = db_session.query(Setting).count()
    events_before = db_session.query(Event).count()

    statements = []

    def capture_statement(connection, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()).upper())

    event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        client = TestClient(main_module.app)
        responses = [client.get("/ready") for _ in range(10)]
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)

    assert all(response.status_code == 200 for response in responses)
    assert all(response.json() == {"status": "ready"} for response in responses)
    assert calls == {"init_db": 0, "seed_defaults": 0, "encrypt_secrets": 0, "dedupe": 0}
    assert statements == ["SELECT 1"] * 10
    assert db_session.query(Setting).count() == settings_before
    assert db_session.query(Event).count() == events_before
    db_session.close()
    engine.dispose()


def test_ready_returns_sanitized_503_when_database_is_unavailable(monkeypatch, tmp_path):
    database_path = tmp_path / "secret-database-name.db"

    class UnavailableEngine:
        def connect(self):
            raise OperationalError("SELECT 1", {}, RuntimeError(str(database_path)))

    monkeypatch.setattr(main_module, "engine", UnavailableEngine(), raising=False)

    response = TestClient(main_module.app).get("/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "Service unavailable"}
    assert str(database_path) not in response.text
    assert "SELECT 1" not in response.text


def _startup_database(tmp_path, monkeypatch):
    database_path = tmp_path / "startup.db"
    engine = create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False})
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(init_db_module, "engine", engine)
    monkeypatch.setattr(session_module, "SessionLocal", session_factory)
    return engine, session_factory


def test_startup_maintenance_runs_once_and_second_start_does_not_scan_events(tmp_path, monkeypatch):
    engine, session_factory = _startup_database(tmp_path, monkeypatch)
    statements = []

    try:
        init_db_module.init_db()
        db = session_factory()
        db.add(
            Event(
                timestamp=datetime(2026, 7, 22, 12, 0, 0),
                event_time=datetime(2026, 7, 22, 12, 0, 0),
                source="test",
                plugin="test",
                event_type="startup.test",
            )
        )
        db.commit()
        db.close()

        def capture_statement(connection, cursor, statement, parameters, context, executemany):
            statements.append(" ".join(statement.split()).lower())

        event.listen(engine, "before_cursor_execute", capture_statement)
        try:
            init_db_module.init_db()
        finally:
            event.remove(engine, "before_cursor_execute", capture_statement)

        db = session_factory()
        marker = db.query(Setting).filter_by(key=init_db_module.EVENT_DEDUPE_MAINTENANCE_KEY).one()
        assert marker.value == init_db_module.EVENT_DEDUPE_MAINTENANCE_VERSION
        db.close()
        assert not any(" from events" in statement for statement in statements)
    finally:
        engine.dispose()


def test_upgrade_without_marker_runs_maintenance_and_existing_marker_skips_it(tmp_path, monkeypatch):
    engine, session_factory = _startup_database(tmp_path, monkeypatch)
    calls = []

    def cleanup(db):
        calls.append("cleanup")
        return 0

    monkeypatch.setattr(events_module, "cleanup_duplicate_events", cleanup)
    try:
        Base.metadata.create_all(bind=engine)
        init_db_module.init_db()
        init_db_module.init_db()

        assert calls == ["cleanup"]
    finally:
        engine.dispose()


def test_failed_startup_maintenance_does_not_write_marker(tmp_path, monkeypatch):
    engine, session_factory = _startup_database(tmp_path, monkeypatch)

    def fail_cleanup(db):
        raise RuntimeError("maintenance failed")

    monkeypatch.setattr(events_module, "cleanup_duplicate_events", fail_cleanup)
    try:
        with pytest.raises(RuntimeError, match="maintenance failed"):
            init_db_module.init_db()

        db = session_factory()
        assert db.query(Setting).filter_by(key=init_db_module.EVENT_DEDUPE_MAINTENANCE_KEY).first() is None
        db.close()
    finally:
        engine.dispose()


def test_programmatic_migration_keeps_one_service_handler(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'logging-migration.db'}"
    monkeypatch.setattr(settings, "database_url", database_url)

    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    stream = io.StringIO()
    root.handlers = []
    try:
        logging_module.setup_service_logging()
        service_handler = next(
            handler
            for handler in root.handlers
            if getattr(handler, "_opensecdash_name", None) == logging_module.SERVICE_HANDLER_NAME
        )
        assert isinstance(service_handler, logging.StreamHandler)
        service_handler.setStream(stream)

        command.upgrade(alembic_config(), "head")
        engine = create_engine(database_url)
        session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db = session_factory()
        db.add_all(
            [
                Setting(key="log_level", value="INFO"),
                Setting(key="log_file_enabled", value="false"),
            ]
        )
        db.commit()
        logging_module.configure_logging_from_db(db)

        logging.getLogger("app.test").info("unique service line")
        service_handler.flush()

        assert stream.getvalue().count("unique service line") == 1
        assert sum(
            getattr(handler, "_opensecdash_name", None) == logging_module.SERVICE_HANDLER_NAME
            for handler in root.handlers
        ) == 1
        db.close()
        engine.dispose()
    finally:
        for handler in root.handlers:
            handler.close()
        root.handlers = original_handlers
        root.setLevel(original_level)


def test_generic_exception_is_logged_once_with_traceback_and_sanitized_request(caplog):
    test_app = FastAPI()
    test_app.add_exception_handler(Exception, main_module.generic_exception_handler)

    @test_app.get("/api/boom")
    def boom():
        raise RuntimeError("intentional failure")

    with caplog.at_level(logging.ERROR, logger="app.main"):
        response = TestClient(test_app, raise_server_exceptions=False).get("/api/boom?token=super-secret")

    matching = [record for record in caplog.records if record.getMessage() == "Unhandled exception for GET /api/boom"]
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert len(matching) == 1
    assert matching[0].exc_info is not None
    assert "token" not in matching[0].getMessage()
    assert "super-secret" not in matching[0].getMessage()


def test_generic_exception_uses_database_independent_fallback(monkeypatch, caplog):
    test_app = FastAPI()
    test_app.add_exception_handler(Exception, main_module.generic_exception_handler)

    @test_app.get("/boom")
    def boom():
        raise RuntimeError("original failure")

    def fail_render(*args, **kwargs):
        raise OperationalError("SELECT settings", {}, RuntimeError("database unavailable"))

    monkeypatch.setattr(main_module, "render_error_page", fail_render)
    with caplog.at_level(logging.ERROR, logger="app.main"):
        response = TestClient(test_app, raise_server_exceptions=False).get("/boom")

    matching = [record for record in caplog.records if record.getMessage() == "Unhandled exception for GET /boom"]
    assert response.status_code == 500
    assert response.text == "Internal server error"
    assert len(matching) == 1
    assert matching[0].exc_info is not None
