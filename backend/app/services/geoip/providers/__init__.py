"""Static registry of the GeoIP providers OpenSecDash ships with.

The mapping is written out here on purpose: no filesystem discovery, no
dynamic import and no configurable module path, so the set of endpoints a
lookup can ever reach is fixed at build time.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from app.services.geoip.providers import ip_api
from app.services.geoip.providers.base import GeoIPProvider

PROVIDERS: Mapping[str, GeoIPProvider] = MappingProxyType({ip_api.PROVIDER.id: ip_api.PROVIDER})


def get_provider(provider_id: str) -> GeoIPProvider:
    provider = PROVIDERS.get(provider_id)
    if provider is None:
        raise ValueError(f"Unsupported GeoIP provider: {provider_id}")
    return provider
