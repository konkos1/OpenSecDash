from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.database.dependencies import get_db
from app.main import app


@pytest.fixture()
def pwa_db(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'pwa.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = session_factory()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def client(pwa_db):
    app.dependency_overrides[get_db] = lambda: pwa_db
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        test_client.close()
        app.dependency_overrides.clear()


def test_service_worker_served_from_root_with_no_cache(client):
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    assert response.headers["cache-control"] == "no-cache"


def test_offline_page_available(client):
    response = client.get("/static/offline.html")
    assert response.status_code == 200


def test_manifest_available(client):
    # Regression guard for installability: the manifest is served from the
    # dynamic /manifest.webmanifest route (app/api/instance.py), not /static/.
    response = client.get("/manifest.webmanifest")
    assert response.status_code == 200


def test_base_page_links_manifest(client):
    response = client.get("/")
    assert response.status_code == 200
    assert '<link rel="manifest" href="/manifest.webmanifest" crossorigin="use-credentials">' in response.text
