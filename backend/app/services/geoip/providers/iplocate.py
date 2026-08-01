"""GeoIP lookups against IPLocate's EU endpoint.

The base URL is a constant on purpose: the endpoint is HTTPS-only and routed
to EU servers by the provider, so neither a configuration mistake nor a
redirect can move the lookup - and with it the API key - somewhere else. The
key travels in a header only, never in the URL, and no error raised here
carries the key, the response body or the full request URL.
"""

from __future__ import annotations

import requests

from app.core.http_responses import read_capped_json
from app.services.geoip.providers.base import (
    GEOIP_RESPONSE_MAX_BYTES,
    GeoIPLookupRequest,
    GeoIPLookupResult,
    GeoIPProvider,
    GeoIPProviderError,
)

PROVIDER_ID = "iplocate"
API_KEY_SETTING = "iplocate_api_key"
LOOKUP_URL = "https://eu-api.iplocate.io/api/lookup/{ip}"
# Only the data OpenSecDash stores. The endpoint would otherwise also return
# threat, VPN/proxy, abuse contact and coordinate information we neither need
# nor want to receive.
RESPONSE_FIELDS = "country_code,city,asn,company,hosting"


def lookup(request: GeoIPLookupRequest) -> GeoIPLookupResult:
    api_key = request.settings.get(API_KEY_SETTING, "").strip()
    if not api_key:
        # No anonymous fallback: an unconfigured provider fails before any
        # address leaves the instance.
        raise GeoIPProviderError("IPLocate API key is not configured")
    response = requests.get(
        LOOKUP_URL.format(ip=request.ip),
        params={"include": RESPONSE_FIELDS},
        headers={"X-API-Key": api_key},
        timeout=request.timeout,
        stream=True,
        allow_redirects=False,
        verify=True,
    )
    try:
        if response.status_code != 200:
            # Deliberately not raise_for_status(): its message repeats the full
            # request URL, which would put the looked-up IP into the cached
            # error and the log line.
            raise _status_error(response.status_code)
        payload = read_capped_json(response, max_bytes=GEOIP_RESPONSE_MAX_BYTES, source="GeoIP provider")
    finally:
        response.close()
    if not isinstance(payload, dict):
        raise GeoIPProviderError("IPLocate returned an invalid response")
    return GeoIPLookupResult(
        country=payload.get("country_code"),
        city=payload.get("city"),
        asn=_nested(payload, "asn", "asn"),
        isp=_first_value(
            _nested(payload, "company", "name"),
            _nested(payload, "hosting", "provider"),
            _nested(payload, "asn", "name"),
        ),
    )


def _status_error(status_code: int) -> GeoIPProviderError:
    if status_code in (401, 403):
        return GeoIPProviderError(f"IPLocate rejected the API key (HTTP {status_code})")
    if status_code == 429:
        return GeoIPProviderError(f"IPLocate quota or rate limit reached (HTTP {status_code})")
    if 300 <= status_code < 400:
        return GeoIPProviderError(f"IPLocate answered with an unexpected redirect (HTTP {status_code})")
    return GeoIPProviderError(f"IPLocate lookup failed (HTTP {status_code})")


def _nested(payload: dict, group: str, key: str) -> object:
    """One value out of an optional sub-object, tolerating any wire shape."""
    group_value = payload.get(group)
    if not isinstance(group_value, dict):
        return None
    return group_value.get(key)


def _first_value(*candidates: object) -> object:
    """First candidate with visible content, still unnormalized for the service."""
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return None


PROVIDER = GeoIPProvider(id=PROVIDER_ID, lookup=lookup, setting_keys=(API_KEY_SETTING,))
