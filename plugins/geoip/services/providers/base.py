"""Plugin-owned contract between the GeoIP service and one remote provider.

A provider module owns exactly one remote API: its endpoint, request shape and
wire format. It never sees the database, plugin metadata, the lookup cache or
the shared field normalization - those stay in the service so adding a provider
cannot change how looked-up data is filtered, stored or reused.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

# Provider answers are streamed and rejected past this size, so a hostile or
# broken endpoint cannot make an enrichment lookup buffer arbitrary memory.
GEOIP_RESPONSE_MAX_BYTES = 64 * 1024


class GeoIPProviderError(RuntimeError):
    """Stable provider failure.

    The message is cached and shown as a lookup error, so it must never carry
    an API key, a raw response body or a full request URL.
    """


class GeoIPConfigurationError(GeoIPProviderError):
    """Provider cannot run until its local configuration changes."""


@dataclass(frozen=True)
class GeoIPLookupRequest:
    """One lookup for an address the service already validated as public."""

    ip: str
    timeout: int
    settings: Mapping[str, str]


@dataclass(frozen=True)
class GeoIPLookupResult:
    """Provider values for the four GeoIP fields OpenSecDash stores.

    Values stay as the provider reported them: length limits, ASN and country
    formatting are applied centrally so every provider yields identical rows.
    """

    country: object = None
    city: object = None
    asn: object = None
    isp: object = None


@dataclass(frozen=True)
class GeoIPProvider:
    """One statically registered provider implementation.

    ``setting_keys`` are short plugin setting names (without the
    ``plugin.geoip.`` prefix). The service resolves exactly those and nothing
    else, so a provider module cannot reach into unrelated configuration.
    """

    id: str
    lookup: Callable[[GeoIPLookupRequest], GeoIPLookupResult]
    setting_keys: tuple[str, ...] = ()
