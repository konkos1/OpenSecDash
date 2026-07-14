from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.database.dependencies import get_db
from app.main import app
from app.services.instance_branding import KIND_FAVICON, get_instance_file, save_instance_file


def png_header(width: int, height: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + width.to_bytes(4, "big") + height.to_bytes(4, "big")


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
    manifest = response.json()
    assert manifest["id"] == "/"
    assert manifest["start_url"] == "/"
    assert not any(icon["src"].endswith(".svg") for icon in manifest["icons"])


def test_manifest_uses_large_custom_favicon_as_pwa_icon(client, pwa_db):
    save_instance_file(pwa_db, KIND_FAVICON, "favicon.png", png_header(512, 512))
    favicon = get_instance_file(pwa_db, KIND_FAVICON)
    assert favicon is not None

    manifest = client.get("/manifest.webmanifest").json()

    assert manifest["icons"][0] == {
        "src": f"/instance/favicon?v={favicon.updated_at}",
        "sizes": "512x512",
        "type": "image/png",
        "purpose": "any",
    }


def test_manifest_ignores_small_custom_favicon_for_pwa(client, pwa_db):
    save_instance_file(pwa_db, KIND_FAVICON, "favicon.png", png_header(64, 64))

    manifest = client.get("/manifest.webmanifest").json()

    assert not any(icon["src"].startswith("/instance/favicon") for icon in manifest["icons"])


def test_base_page_links_manifest(client):
    response = client.get("/")
    assert response.status_code == 200
    assert '<link rel="manifest" href="/manifest.webmanifest" crossorigin="use-credentials">' in response.text
