"""Provider-neutral GeoIP orchestration.

Public-IP filtering, settings resolution, caching, TTLs, the error cache and
the producer-wins enrichment live here once. Everything endpoint-specific -
URLs, headers, wire field names - lives in ``providers/``.
"""

from __future__ import annotations

import ipaddress
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from types import MappingProxyType
from typing import Any

from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.core.time import utc_now
from app.models.core import AggregationDaily, GeoIPCache
from app.models.events import Event
from app.services.events import create_rule_based_insights
from app.services.notifications import handle_event
from .providers import get_provider
from .providers.base import GeoIPConfigurationError, GeoIPLookupRequest, GeoIPProvider

logger = logging.getLogger(__name__)

ERROR_CACHE_TTL = timedelta(hours=1)


@dataclass(frozen=True)
class _GeoIPLookupOutcome:
    values: tuple[str | None, str | None, str | None, str | None]
    complete: bool


def geoip_enabled(db: Session) -> bool:
    return get_setting_value(db, "plugin.geoip.enabled", "false") == "true"


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


def enrich_event_values(db: Session, values: dict[str, Any]) -> bool:
    """Add GeoIP-derived fields to event values when applicable.

    Producers win: if a plugin already supplied ``country``, ``city``, ``asn``
    or ``isp`` we never overwrite that field.
    """
    if not geoip_enabled(db):
        return False
    if values.get("country") and values.get("city") and values.get("asn") and values.get("isp"):
        return True
    outcome = _lookup_geoip_outcome(
        db,
        values.get("ip"),
        require_city=not bool(values.get("city")),
        require_asn=not bool(values.get("asn")),
        require_isp=not bool(values.get("isp")),
    )
    country, city, asn, isp = outcome.values
    if country and not values.get("country"):
        values["country"] = country
    if city and not values.get("city"):
        values["city"] = city
    if asn and not values.get("asn"):
        values["asn"] = asn
    if isp and not values.get("isp"):
        values["isp"] = isp
    return outcome.complete


def lookup_country(db: Session, ip_or_range: str | None) -> str | None:
    country, _city, _asn, _isp = lookup_geoip(db, ip_or_range)
    return country


def lookup_geoip(
    db: Session,
    ip_or_range: str | None,
    require_city: bool = False,
    require_asn: bool = False,
    require_isp: bool = False,
) -> tuple[str | None, str | None, str | None, str | None]:
    return _lookup_geoip_outcome(db, ip_or_range, require_city, require_asn, require_isp).values


def _lookup_geoip_outcome(
    db: Session,
    ip_or_range: str | None,
    require_city: bool = False,
    require_asn: bool = False,
    require_isp: bool = False,
) -> _GeoIPLookupOutcome:
    target = normalize_lookup_target(ip_or_range)
    if target is None:
        return _GeoIPLookupOutcome((None, None, None, None), complete=True)
    lookup_key, lookup_ip = target
    provider = get_setting_value(db, "plugin.geoip.provider", "iplocate")
    ttl_days = _int_setting(db, "plugin.geoip.cache_ttl_days", 30, minimum=1)
    now = utc_now().replace(tzinfo=None)

    cached = _pending_cache_row(db, lookup_key)
    if cached is None:
        with db.no_autoflush:
            cached = db.query(GeoIPCache).filter(GeoIPCache.lookup_key == lookup_key).first()
    if (
        cached is not None
        and cached.expires_at > now
        # Data another provider looked up is not a hit for the currently
        # selected one: after a provider switch the row is refreshed on the
        # next lookup instead of serving the old provider's answer until the
        # TTL runs out.
        and cached.provider == provider
        and (not require_city or cached.city is not None or cached.error)
        and (not require_asn or cached.asn is not None or cached.error)
        and (not require_isp or cached.isp is not None or cached.error)
    ):
        return _GeoIPLookupOutcome(
            (cached.country, cached.city, cached.asn, cached.isp),
            complete=not bool(cached.error),
        )

    try:
        country, city, asn, isp = _lookup_provider_geoip(db, provider, lookup_ip)
    except GeoIPConfigurationError as exc:
        # Configuration can be corrected at any time. Do not poison the cache:
        # the next enrichment pass should use a newly saved key immediately.
        logger.warning("GeoIP lookup skipped provider=%s: %s", provider, exc)
        return _GeoIPLookupOutcome((None, None, None, None), complete=False)
    except Exception as exc:
        logger.warning("GeoIP lookup failed provider=%s target=%s: %s", provider, lookup_key, exc)
        _store_cache(db, cached, lookup_key, provider, None, None, None, None, now, now + ERROR_CACHE_TTL, str(exc), now)
        return _GeoIPLookupOutcome((None, None, None, None), complete=False)

    _store_cache(db, cached, lookup_key, provider, country, city, asn, isp, now, now + timedelta(days=ttl_days), None, None)
    return _GeoIPLookupOutcome((country, city, asn, isp), complete=True)


def _lookup_provider_geoip(db: Session, provider: str, lookup_ip: str) -> tuple[str | None, str | None, str | None, str | None]:
    implementation = get_provider(provider)
    result = implementation.lookup(
        GeoIPLookupRequest(
            ip=lookup_ip,
            timeout=_int_setting(db, "plugin.geoip.timeout_seconds", 3, minimum=1),
            settings=_provider_settings(db, implementation),
        )
    )
    return (
        normalize_country(result.country),
        normalize_city(result.city),
        normalize_asn(result.asn),
        normalize_isp(result.isp),
    )


def _provider_settings(db: Session, provider: GeoIPProvider) -> Mapping[str, str]:
    return MappingProxyType({key: get_setting_value(db, f"plugin.geoip.{key}", "") for key in provider.setting_keys})


def normalize_country(value: object) -> str | None:
    text = str(value or "").strip().upper()
    return text if len(text) == 2 else None


def normalize_asn(value: object) -> str | None:
    text = str(value or "").strip().upper()
    if not text:
        return None
    first = text.split()[0]
    if first.startswith("AS") and first[2:].isdigit():
        return first
    if first.isdigit():
        return f"AS{first}"
    return None


def normalize_city(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text[:255]


def normalize_isp(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text[:255]


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
    city: str | None,
    asn: str | None,
    isp: str | None,
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
    row.city = city
    row.asn = asn
    row.isp = isp
    row.looked_up_at = looked_up_at
    row.expires_at = expires_at
    row.error = error
    row.last_error_at = last_error_at


def cleanup_expired_cache(db: Session) -> int:
    now = utc_now().replace(tzinfo=None)
    deleted = db.query(GeoIPCache).filter(GeoIPCache.expires_at < now).delete()
    return int(deleted or 0)


def _reconcile_country_derived_data(db: Session, event: Event) -> None:
    """Add data that could not be derived before async GeoIP enrichment."""
    if not event.country:
        return
    day = event.event_time.strftime("%Y-%m-%d")
    country_rollup = (
        db.query(AggregationDaily)
        .filter(
            AggregationDaily.date == day,
            AggregationDaily.metric == "country",
            AggregationDaily.key == event.country,
        )
        .first()
    )
    if country_rollup is None:
        db.add(AggregationDaily(date=day, metric="country", key=event.country, value=1))
    else:
        country_rollup.value += 1

    # Both paths are safe to repeat: insights deduplicate by their related
    # event IDs and event notifications deduplicate by rule and event ID.
    create_rule_based_insights(db, event)
    handle_event(db, event)


def enrich_pending_events(db: Session, limit: int = 50) -> int:
    """Backfill GeoIP fields for recently stored events, a few at a time.

    Ingestion (``store_event``) never enriches inline anymore - a fresh import
    of a large log can mean thousands of uncached IPs, and doing that many
    synchronous lookup requests during ingestion is what used to make the
    whole app stall. This runs on its own paced background loop instead, so a
    slow/unreachable GeoIP provider can only ever delay when country/city/ASN
    show up, not block anything else.

    Committing per-event (rather than once for the whole batch) means a
    slow/blocking GeoIP lookup only ever holds the SQLite write lock for one
    event's worth of time, not the whole batch - the app-wide write lock in
    ``app.database.session`` is acquired automatically on each commit.
    """
    if not geoip_enabled(db):
        return 0
    events = (
        db.query(Event)
        .filter(Event.geoip_checked == False, Event.ip.isnot(None), Event.ip != "")  # noqa: E712
        .order_by(Event.id.desc())
        .limit(limit)
        .all()
    )
    processed = 0
    for event in events:
        previous_country = event.country
        values: dict[str, Any] = {
            "ip": event.ip,
            "country": event.country,
            "city": event.city,
            "asn": event.asn,
            "isp": event.isp,
        }
        complete = enrich_event_values(db, values)
        event.country = values.get("country") or event.country
        event.city = values.get("city") or event.city
        event.asn = values.get("asn") or event.asn
        event.isp = values.get("isp") or event.isp
        if complete and not previous_country and event.country:
            _reconcile_country_derived_data(db, event)
        event.geoip_checked = complete
        if complete:
            processed += 1
        db.commit()
    return processed
