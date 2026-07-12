from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.models.assets import Asset
from app.models.events import Event
from app.core.time import utc_now


def asset_stale_threshold(source_plugin: str | None) -> timedelta:
    if source_plugin == "proxmox_assets":
        return timedelta(hours=24)
    return timedelta(days=7)


def asset_last_seen_stale(last_seen: datetime | None, source_plugin: str | None, now: datetime | None = None) -> bool:
    if last_seen is None:
        return True
    current = now or utc_now().replace(tzinfo=None)
    return current - last_seen > asset_stale_threshold(source_plugin)


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


_HOST_ASSET_CACHE_KEY = "_opensecdash_asset_host_map"


def _asset_host_map(db: Session) -> dict[str, int]:
    """Map of normalized host -> asset id, cached for the session's lifetime.

    ``find_asset_by_host`` runs once per stored event (the ingestion hot
    path); without the cache a 50k-line import re-reads and re-normalizes the
    whole asset table 50k times over. Sessions are short-lived (one request /
    one datasource tick), so staleness is bounded to a single batch.
    """
    cached = db.info.get(_HOST_ASSET_CACHE_KEY)
    if cached is None:
        cached = {}
        for asset_id, host_url in db.query(Asset.id, Asset.host_url).filter(Asset.host_url.isnot(None), Asset.is_active == True).order_by(Asset.id).all():
            normalized = normalize_asset_host(host_url)
            if normalized:
                cached.setdefault(normalized, asset_id)
        db.info[_HOST_ASSET_CACHE_KEY] = cached
    return cached


def find_asset_by_host(db: Session, host: str | None) -> Asset | None:
    normalized_host = normalize_asset_host(host)
    if not normalized_host:
        return None
    asset_id = _asset_host_map(db).get(normalized_host)
    if asset_id is None:
        return None
    return db.query(Asset).filter(Asset.id == asset_id).first()


def matching_event_hostnames(db: Session, normalized_host: str | None) -> list[str]:
    """Raw ``events.hostname`` values that normalize to ``normalized_host``.

    Host matching needs Python-side normalization (scheme/port/case), which
    used to mean loading every event row into memory. The set of DISTINCT
    hostnames is tiny by comparison (one per vhost), so normalizing those and
    letting SQL filter by ``hostname IN (...)`` gives the same result at a
    fraction of the cost.
    """
    if not normalized_host:
        return []
    return [
        hostname
        for (hostname,) in db.query(Event.hostname).filter(Event.hostname.isnot(None)).distinct().all()
        if normalize_asset_host(hostname) == normalized_host
    ]


def event_matches_asset_host(event: Event, asset: Asset) -> bool:
    asset_host = normalize_asset_host(asset.host_url)
    event_host = normalize_asset_host(event.hostname)
    return bool(asset_host and event_host and asset_host == event_host)


def sync_asset_host_events(db: Session, asset: Asset) -> int:
    """Rebuild host-based event mapping for one asset.

    Host mappings are derived data. When a user edits ``asset.host_url`` we must
    both attach newly matching events and detach events that matched the previous
    host, otherwise tables would keep stale links via ``events.asset_id``.

    Runs as two bulk UPDATEs over the DISTINCT-hostname match set instead of
    iterating every event row - same result, but independent of table size.
    """
    # Flush first: expire_all() below would otherwise silently discard any
    # not-yet-flushed changes the caller staged on this session (e.g. the
    # asset edits in update_asset_metadata that trigger this sync).
    db.flush()
    asset_host = normalize_asset_host(asset.host_url)
    matched_hostnames = matching_event_hostnames(db, asset_host)

    attached = 0
    if matched_hostnames:
        attached = (
            db.query(Event)
            .filter(
                Event.hostname.in_(matched_hostnames),
                (Event.asset_id != asset.id) | (Event.asset_id.is_(None)),
            )
            .update({Event.asset_id: asset.id}, synchronize_session=False)
        )

    detach_query = db.query(Event).filter(Event.asset_id == asset.id)
    if matched_hostnames:
        detach_query = detach_query.filter((Event.hostname.notin_(matched_hostnames)) | (Event.hostname.is_(None)))
    detached = detach_query.update({Event.asset_id: None}, synchronize_session=False)

    # The bulk UPDATEs bypass the identity map (synchronize_session=False);
    # expire so already-loaded Event objects don't keep stale asset_id values.
    db.expire_all()
    return int(attached or 0) + int(detached or 0)
