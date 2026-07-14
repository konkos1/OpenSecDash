"""Persist and serve the instance-specific logo and favicon."""
from pathlib import Path
import time

from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError

from app.models.settings import InstanceFile

KIND_LOGO = "logo"
KIND_FAVICON = "favicon"

MAX_BYTES = {KIND_LOGO: 1_000_000, KIND_FAVICON: 512_000}

ALLOWED_TYPES = {
    KIND_LOGO: {"image/png", "image/svg+xml", "image/webp", "image/jpeg"},
    KIND_FAVICON: {"image/png", "image/svg+xml", "image/webp", "image/x-icon"},
}


def detect_image_type(data: bytes) -> str | None:
    """Identify supported image formats from their file signatures."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x00\x00\x01\x00"):
        return "image/x-icon"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"

    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None

    if "<svg" in text.lstrip("\ufeff \t\r\n").lower()[:1000]:
        return "image/svg+xml"
    return None


def pwa_icon_size(data: bytes, content_type: str) -> str | None:
    """Return a usable manifest size for a scalable or large square favicon."""
    if content_type == "image/svg+xml":
        # Chromium accepts SVG manifest icons when a concrete target size is
        # advertised. The vector remains suitable at other rendered sizes.
        return "512x512"

    width: int | None = None
    height: int | None = None
    if content_type == "image/png" and len(data) >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n"):
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
    elif content_type == "image/webp" and len(data) >= 30 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        chunk = data[12:16]
        if chunk == b"VP8X":
            width = int.from_bytes(data[24:27], "little") + 1
            height = int.from_bytes(data[27:30], "little") + 1
        elif chunk == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
            width = int.from_bytes(data[26:28], "little") & 0x3FFF
            height = int.from_bytes(data[28:30], "little") & 0x3FFF
        elif chunk == b"VP8L" and data[20] == 0x2F:
            dimensions = int.from_bytes(data[21:25], "little")
            width = (dimensions & 0x3FFF) + 1
            height = ((dimensions >> 14) & 0x3FFF) + 1

    if width is None or height is None or width != height or width < 512:
        return None
    return f"{width}x{height}"


def validate_upload(kind: str, data: bytes) -> str:
    """Validate an uploaded instance image and return its detected type."""
    if not data:
        raise ValueError("empty")
    if len(data) > MAX_BYTES[kind]:
        raise ValueError("too_large")

    content_type = detect_image_type(data)
    if content_type not in ALLOWED_TYPES[kind]:
        raise ValueError("invalid_type")
    return content_type


def save_instance_file(db: Session, kind: str, filename: str, data: bytes, *, commit: bool = True) -> None:
    """Store one validated file for an instance branding kind."""
    content_type = validate_upload(kind, data)
    row = get_instance_file(db, kind)
    if row is None:
        row = InstanceFile(
            kind=kind,
            filename=Path(filename).name[:255],
            content_type=content_type,
            data=data,
            updated_at=int(time.time()),
        )
        db.add(row)
    else:
        row.filename = Path(filename).name[:255]
        row.content_type = content_type
        row.data = data
        row.updated_at = int(time.time())
    if commit:
        db.commit()


def get_instance_file(db: Session, kind: str) -> InstanceFile | None:
    """Return the stored file for a branding kind, if present."""
    return db.query(InstanceFile).filter(InstanceFile.kind == kind).first()


def delete_instance_file(db: Session, kind: str, *, commit: bool = True) -> None:
    """Remove a branding file when one has been stored."""
    row = get_instance_file(db, kind)
    if row is not None:
        db.delete(row)
        if commit:
            db.commit()


def instance_file_versions(db: Session) -> dict[str, int | None]:
    """Return cache-busting versions without loading the image BLOBs."""
    versions: dict[str, int | None] = {KIND_LOGO: None, KIND_FAVICON: None}
    try:
        for kind, updated_at in db.query(InstanceFile.kind, InstanceFile.updated_at).all():
            versions[kind] = updated_at
    except OperationalError:
        db.rollback()
    return versions
