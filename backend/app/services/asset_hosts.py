from __future__ import annotations

from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.models.assets import Asset


def normalize_asset_host(value: str | None) -> str | None:
    """Return a canonical lowercase host from a user-entered asset URL/host.

    Users may enter a full URL (``https://app.example.com/path``) or only a host
    (``app.example.com``). Keeping this logic small and side-effect free makes it
    straightforward to cover with unit tests once the test suite is added.
    """
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    parsed = urlsplit(text if "://" in text else f"//{text}")
    host = parsed.hostname or text.split("/", 1)[0]
    host = host.strip().lower().rstrip(".")
    return host or None


def find_asset_by_host(db: Session, host: str | None) -> Asset | None:
    normalized_host = normalize_asset_host(host)
    if not normalized_host:
        return None
    for asset in db.query(Asset).filter(Asset.host_url.isnot(None), Asset.is_active == True).all():
        if normalize_asset_host(asset.host_url) == normalized_host:
            return asset
    return None
