"""GeoIP lookups against ip-api.com.

Legacy provider: its free endpoint is plain HTTP, which is why the settings UI
labels the option as unencrypted. Kept unchanged so existing installations that
selected it keep working exactly as before.
"""

from __future__ import annotations

import requests

from app.core.http_responses import read_capped_json
from .base import (
    GEOIP_RESPONSE_MAX_BYTES,
    GeoIPLookupRequest,
    GeoIPLookupResult,
    GeoIPProvider,
    GeoIPProviderError,
)

PROVIDER_ID = "ip-api"
LOOKUP_URL = "http://ip-api.com/json/{ip}"
# Only the fields OpenSecDash stores, plus the status/message pair ip-api.com
# uses to report failures - the endpoint returns far more by default.
RESPONSE_FIELDS = "status,countryCode,city,as,isp,message"


def lookup(request: GeoIPLookupRequest) -> GeoIPLookupResult:
    response = requests.get(
        LOOKUP_URL.format(ip=request.ip),
        params={"fields": RESPONSE_FIELDS},
        timeout=request.timeout,
        stream=True,
    )
    try:
        response.raise_for_status()
        payload = read_capped_json(response, max_bytes=GEOIP_RESPONSE_MAX_BYTES, source="GeoIP provider")
    finally:
        response.close()
    if not isinstance(payload, dict):
        raise GeoIPProviderError("GeoIP provider returned an invalid response")
    if payload.get("status") != "success":
        raise GeoIPProviderError(str(payload.get("message") or "GeoIP lookup failed"))
    return GeoIPLookupResult(
        country=payload.get("countryCode"),
        city=payload.get("city"),
        asn=payload.get("as"),
        isp=payload.get("isp"),
    )


PROVIDER = GeoIPProvider(id=PROVIDER_ID, lookup=lookup)
