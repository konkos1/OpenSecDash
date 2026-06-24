from __future__ import annotations

import ipaddress
import logging
from datetime import timedelta
from typing import Any

import requests
from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.core.time import utc_now
from app.models.core import GeoIPCache

logger = logging.getLogger(__name__)

ERROR_CACHE_TTL = timedelta(hours=1)


def geoip_enabled(db: Session) -> bool:
    return get_setting_value(db, "plugin.geoip.enabled", "true") == "true"


def _int_setting(db: Session, key: str, default: int, minimum: int = 0) -> int:
    try:
        return max(int(get_setting_value(db, key, str(default))), minimum)
    except ValueError:
        return default


def normalize_lookup_target(value: str | None) -> tuple[str, str] | None:
    """Normalize an IP/CIDR input and choose the address sent to GeoIP.

    Returns ``(cache_key, lookup_ip)``. Local/private/reserved ranges return
    ``None`` so callers can skip remote lookups. This function is intentionally
    pure and is a good target for future tests covering IPv4, IPv6 and CIDR.
    """
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if "/" in text:
            network = ipaddress.ip_network(text, strict=False)
            if _is_non_public_network(network):
                return None
            lookup_ip = network.network_address
            if network.version == 4 and network.num_addresses > 2:
                lookup_ip = network.network_address + 1
            return network.with_prefixlen, str(lookup_ip)
        address = ipaddress.ip_address(text)
        if _is_non_public_address(address):
            return None
        return str(address), str(address)
    except ValueError:
        logger.debug("Skipping GeoIP enrichment for invalid IP/range: %s", text)
        return None


def _is_non_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _is_non_public_network(network: ipaddress._BaseNetwork) -> bool:
    return (
        network.is_private
        or network.is_loopback
        or network.is_link_local
        or network.is_multicast
        or network.is_reserved
        or network.is_unspecified
    )


def enrich_event_values(db: Session, values: dict[str, Any]) -> None:
    """Add ``country`` to event values when GeoIP is enabled and applicable.

    Producers win: if a plugin already supplied a country we never overwrite it.
    The event ingestion path calls this before rollups/insights so derived data
    sees the enriched country immediately.
    """
    if values.get("country") or not geoip_enabled(db):
        return
    country = lookup_country(db, values.get("ip"))
    if country:
        values["country"] = country


def lookup_country(db: Session, ip_or_range: str | None) -> str | None:
    target = normalize_lookup_target(ip_or_range)
    if target is None:
        return None
    lookup_key, lookup_ip = target
    provider = get_setting_value(db, "plugin.geoip.provider", "ip-api")
    ttl_days = _int_setting(db, "plugin.geoip.cache_ttl_days", 30, minimum=1)
    now = utc_now().replace(tzinfo=None)

    cached = _pending_cache_row(db, lookup_key)
    if cached is None:
        with db.no_autoflush:
            cached = db.query(GeoIPCache).filter(GeoIPCache.lookup_key == lookup_key).first()
    if cached is not None and cached.expires_at > now:
        return cached.country

    try:
        country = _lookup_provider_country(db, provider, lookup_ip)
    except Exception as exc:
        logger.warning("GeoIP lookup failed provider=%s target=%s: %s", provider, lookup_key, exc)
        _store_cache(db, cached, lookup_key, provider, None, now, now + ERROR_CACHE_TTL, str(exc), now)
        return None

    _store_cache(db, cached, lookup_key, provider, country, now, now + timedelta(days=ttl_days), None, None)
    return country


def _lookup_provider_country(db: Session, provider: str, lookup_ip: str) -> str | None:
    timeout = _int_setting(db, "plugin.geoip.timeout_seconds", 3, minimum=1)
    if provider == "ip-api":
        response = requests.get(
            f"http://ip-api.com/json/{lookup_ip}",
            params={"fields": "status,countryCode,message"},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "success":
            raise RuntimeError(str(payload.get("message") or "GeoIP lookup failed"))
        country = str(payload.get("countryCode") or "").upper()
        return country if len(country) == 2 else None
    raise ValueError(f"Unsupported GeoIP provider: {provider}")


def _pending_cache_row(db: Session, lookup_key: str) -> GeoIPCache | None:
    # A datasource import can enrich hundreds of events before the session is
    # committed. Pending ORM rows are not returned by normal queries, so check
    # ``db.new`` first to avoid inserting duplicate cache rows for the same IP.
    for item in db.new:
        if isinstance(item, GeoIPCache) and item.lookup_key == lookup_key:
            return item
    return None


def _store_cache(
    db: Session,
    cached: GeoIPCache | None,
    lookup_key: str,
    provider: str,
    country: str | None,
    looked_up_at,
    expires_at,
    error: str | None,
    last_error_at,
) -> None:
    row = cached or GeoIPCache(lookup_key=lookup_key)
    if cached is None:
        db.add(row)
    row.provider = provider
    row.country = country
    row.looked_up_at = looked_up_at
    row.expires_at = expires_at
    row.error = error
    row.last_error_at = last_error_at


def cleanup_expired_cache(db: Session) -> int:
    now = utc_now().replace(tzinfo=None)
    deleted = db.query(GeoIPCache).filter(GeoIPCache.expires_at < now).delete()
    return int(deleted or 0)
