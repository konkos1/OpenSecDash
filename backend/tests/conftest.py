from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core import secrets as secrets_module
from app.database.base import Base
from app.models import *  # noqa: F403 - import models so SQLAlchemy registers all tables

PLUGINS_DIR = Path(__file__).resolve().parents[2] / "plugins"


def import_plugin_module(plugin_dirname: str, submodule: str):
    """Import a plugin submodule the same way the plugin manager loads it.

    Tests for plugin-owned services (see docs/internal/plugin-rework/) use this
    instead of sys.path tricks, so test imports match the deployed layout.
    """
    from app.plugins.loader import import_plugin_module as _import

    return _import(PLUGINS_DIR / plugin_dirname, submodule)


@pytest.fixture(autouse=True, scope="session")
def _plugin_registry():
    # Discovery imports the real plugin modules once so the plugin registry and
    # locales are populated - feature flags, nav and i18n now read from there
    # (see app.core.plugin_registry / app.core.i18n).
    from app.plugins.manager import get_plugin_manager

    get_plugin_manager()


@pytest.fixture(autouse=True)
def _test_secret_key(monkeypatch):
    # Every test runs with a fixed in-memory encryption key so no test can
    # ever auto-generate a real key file next to the developer's local DB.
    monkeypatch.setenv(secrets_module.SECRET_KEY_ENV, Fernet.generate_key().decode("ascii"))
    secrets_module.reset_secret_key_cache()
    yield
    secrets_module.reset_secret_key_cache()


@pytest.fixture(autouse=True)
def _browser_origin_header(monkeypatch):
    """Make application TestClients send the Origin header supplied by browsers."""
    original_init = TestClient.__init__
    monkeypatch.setenv("OSD_TRUSTED_PROXIES", "127.0.0.1")

    def init_with_origin(self, *args, **kwargs):
        kwargs.setdefault("client", ("127.0.0.1", 50000))
        original_init(self, *args, **kwargs)
        external_origin = str(self.base_url).rstrip("/")
        self.headers.setdefault("origin", external_origin)
        self.headers.setdefault("x-forwarded-proto", self.base_url.scheme)
        self.headers.setdefault("x-forwarded-host", external_origin.split("://", 1)[1])
        self.headers.setdefault("x-forwarded-port", str(self.base_url.port or (443 if self.base_url.scheme == "https" else 80)))

    monkeypatch.setattr(TestClient, "__init__", init_with_origin)


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
