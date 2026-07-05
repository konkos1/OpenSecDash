import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core import secrets as secrets_module
from app.database.base import Base
from app.models import *  # noqa: F403 - import models so SQLAlchemy registers all tables


@pytest.fixture(autouse=True)
def _test_secret_key(monkeypatch):
    # Every test runs with a fixed in-memory encryption key so no test can
    # ever auto-generate a real key file next to the developer's local DB.
    monkeypatch.setenv(secrets_module.SECRET_KEY_ENV, Fernet.generate_key().decode("ascii"))
    secrets_module.reset_secret_key_cache()
    yield
    secrets_module.reset_secret_key_cache()


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
