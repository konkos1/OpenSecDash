from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.database.dependencies import get_db
from app.main import app
from app.models.settings import InstanceFile
from app.services import instance_branding


PNG_DATA = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG_DATA = b"\xff\xd8\xff" + b"\x00" * 16
ICO_DATA = b"\x00\x00\x01\x00" + b"\x00" * 16
WEBP_DATA = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 16
SVG_DATA = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>'


@pytest.fixture()
def instance_branding_db(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'instance-branding.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = session_factory()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.mark.parametrize(
    ("data", "content_type"),
    [
        (PNG_DATA, "image/png"),
        (JPEG_DATA, "image/jpeg"),
        (ICO_DATA, "image/x-icon"),
        (WEBP_DATA, "image/webp"),
        (SVG_DATA, "image/svg+xml"),
    ],
)
def test_detect_image_type_recognizes_supported_formats(data, content_type):
    assert instance_branding.detect_image_type(data) == content_type


@pytest.mark.parametrize("data", [b"GIF89a", b"random bytes", b""])
def test_detect_image_type_rejects_unknown_formats(data):
    assert instance_branding.detect_image_type(data) is None


def test_validate_upload_enforces_size_and_allowed_types():
    with pytest.raises(ValueError, match="^too_large$"):
        instance_branding.validate_upload(instance_branding.KIND_LOGO, PNG_DATA + b"x" * 1_000_000)
    with pytest.raises(ValueError, match="^invalid_type$"):
        instance_branding.validate_upload(instance_branding.KIND_FAVICON, b"GIF89a")
    with pytest.raises(ValueError, match="^invalid_type$"):
        instance_branding.validate_upload(instance_branding.KIND_LOGO, ICO_DATA)

    assert instance_branding.validate_upload(instance_branding.KIND_FAVICON, ICO_DATA) == "image/x-icon"


def test_instance_file_roundtrip_versions_and_deletion(db_session, monkeypatch):
    times = iter([100, 101])
    monkeypatch.setattr(instance_branding.time, "time", lambda: next(times))

    instance_branding.save_instance_file(db_session, instance_branding.KIND_LOGO, "../first.png", PNG_DATA)
    stored = instance_branding.get_instance_file(db_session, instance_branding.KIND_LOGO)

    assert stored is not None
    assert stored.filename == "first.png"
    assert stored.data == PNG_DATA
    assert stored.content_type == "image/png"
    assert stored.updated_at == 100
    assert instance_branding.instance_file_versions(db_session) == {"logo": 100, "favicon": None}

    instance_branding.save_instance_file(db_session, instance_branding.KIND_LOGO, "second.png", SVG_DATA)

    assert db_session.query(InstanceFile).filter(InstanceFile.kind == instance_branding.KIND_LOGO).count() == 1
    updated = instance_branding.get_instance_file(db_session, instance_branding.KIND_LOGO)
    assert updated is not None
    assert updated.updated_at == 101

    instance_branding.delete_instance_file(db_session, instance_branding.KIND_LOGO)

    assert instance_branding.get_instance_file(db_session, instance_branding.KIND_LOGO) is None
    assert instance_branding.instance_file_versions(db_session) == {"logo": None, "favicon": None}


@pytest.mark.parametrize(
    ("path", "kind", "filename"),
    [
        ("/instance/logo", instance_branding.KIND_LOGO, "logo.png"),
        ("/instance/favicon", instance_branding.KIND_FAVICON, "favicon.png"),
    ],
)
def test_instance_file_routes_return_files_with_security_headers(instance_branding_db, path, kind, filename):
    app.dependency_overrides[get_db] = lambda: instance_branding_db
    client = TestClient(app)
    try:
        missing = client.get(path)
        instance_branding.save_instance_file(instance_branding_db, kind, filename, PNG_DATA)
        response = client.get(path)
    finally:
        client.close()
        app.dependency_overrides.clear()

    assert missing.status_code == 404
    assert response.status_code == 200
    assert response.content == PNG_DATA
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "public, max-age=86400"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "default-src 'none'; style-src 'unsafe-inline'"
