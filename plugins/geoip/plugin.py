from __future__ import annotations

import logging
import time

from app.plugins.base import EnrichmentPlugin, PluginContext, PluginMetadata, PluginSetting

from .services import cleanup_expired_cache, enrich_pending_event_batch, latest_provider_attempt

logger = logging.getLogger(__name__)
CACHE_CLEANUP_INTERVAL_SECONDS = 60
IPLOCATE_UNREACHABLE_ERRORS = frozenset(
    {
        "IPLocate request could not be sent",
        "IPLocate response could not be read",
    }
)


class Plugin(EnrichmentPlugin):
    metadata = PluginMetadata(
        id="geoip",
        name="GeoIP / ASN / ISP / City Enrichment",
        version="1.0.0",
        api_version="2",
        capabilities=["enrichment"],
        description="Adds country codes, cities, ASNs and ISPs to public IP events using a cached provider lookup.",
    )
    settings = [
        PluginSetting("enabled", "geoip.settings.enabled", "geoip.settings.enabled.help", "boolean", "false", [("false", "common.no"), ("true", "common.yes")]),
        PluginSetting(
            "provider",
            "geoip.settings.provider",
            "geoip.settings.provider.help",
            "select",
            # Only a missing provider row is seeded with this default, so a new
            # installation pre-selects IPLocate while an existing installation
            # keeps whatever it chose - including ip-api.
            "iplocate",
            [("iplocate", "geoip.option.iplocate"), ("ip-api", "geoip.option.ip_api")],
            visible_if=("enabled", "true"),
            option_info=[("iplocate", "geoip.option.iplocate.info"), ("ip-api", "geoip.option.ip_api.info")],
        ),
        PluginSetting(
            "iplocate_api_key",
            "geoip.settings.iplocate_api_key",
            "geoip.settings.iplocate_api_key.help",
            "password",
            "",
            visible_if_all=(("enabled", "true"), ("provider", "iplocate")),
        ),
        PluginSetting("cache_ttl_days", "geoip.settings.cache_ttl_days", "geoip.settings.cache_ttl_days.help", "number", "30", visible_if=("enabled", "true")),
        PluginSetting("timeout_seconds", "geoip.settings.timeout_seconds", "geoip.settings.timeout_seconds.help", "number", "3", visible_if=("enabled", "true")),
    ]
    locales = {
        "en": {
            "geoip.settings.enabled": "GeoIP enabled",
            "geoip.settings.enabled.help": "Enables GeoIP, ASN, ISP and city enrichment for events with public IP addresses.",
            "geoip.settings.provider": "GeoIP provider",
            "geoip.settings.provider.help": "Selects the provider used for GeoIP lookups. Note the transport and privacy information shown with the selected provider.",
            "geoip.option.iplocate": "IPLocate EU endpoint (encrypted HTTPS)",
            "geoip.option.iplocate.info": (
                "Full GeoIP enrichment with country, city, ASN, and provider/ISP. Uncached public IP addresses are sent "
                "over encrypted HTTPS to IPLocate's EU endpoint."
            ),
            "geoip.option.ip_api": "ip-api.com (unencrypted HTTP)",
            "geoip.option.ip_api.info": "Uncached public IPs are sent to ip-api.com over unencrypted HTTP.",
            "geoip.settings.iplocate_api_key": "IPLocate API key",
            "geoip.settings.iplocate_api_key.help": (
                "Required for IPLocate lookups; a free account is enough for a homelab. The key is stored encrypted, "
                "sent only in a request header and never shown again."
            ),
            "geoip.settings.cache_ttl_days": "GeoIP cache TTL days",
            "geoip.settings.cache_ttl_days.help": "How long successful GeoIP lookups stay cached before being refreshed.",
            "geoip.settings.timeout_seconds": "GeoIP timeout seconds",
            "geoip.settings.timeout_seconds.help": "HTTP timeout for one GeoIP provider request.",
            "common.yes": "Yes",
            "common.no": "No",
        },
        "de": {
            "geoip.settings.enabled": "GeoIP aktiviert",
            "geoip.settings.enabled.help": "Aktiviert die GeoIP-, ASN-, ISP- und Stadt-Anreicherung für Events mit öffentlichen IP-Adressen.",
            "geoip.settings.provider": "GeoIP-Provider",
            "geoip.settings.provider.help": "Wählt den Provider für GeoIP-Abfragen aus. Beachte die beim ausgewählten Provider angezeigten Transport- und Datenschutzhinweise.",
            "geoip.option.iplocate": "IPLocate EU-Endpunkt (verschlüsseltes HTTPS)",
            "geoip.option.iplocate.info": (
                "Vollständige GeoIP-Anreicherung mit Land, Stadt, ASN und Provider/ISP. Nicht gecachte öffentliche "
                "IP-Adressen werden HTTPS-verschlüsselt an den EU-Endpunkt von IPLocate übertragen."
            ),
            "geoip.option.ip_api": "ip-api.com (unverschlüsselt)",
            "geoip.option.ip_api.info": "Nicht gecachte öffentliche IPs werden unverschlüsselt an ip-api.com gesendet.",
            "geoip.settings.iplocate_api_key": "IPLocate-API-Key",
            "geoip.settings.iplocate_api_key.help": (
                "Für IPLocate-Abfragen erforderlich; ein kostenloses Konto reicht für ein Homelab. Der Key wird "
                "verschlüsselt gespeichert, nur im Request-Header gesendet und nicht erneut angezeigt."
            ),
            "geoip.settings.cache_ttl_days": "GeoIP-Cache-TTL in Tagen",
            "geoip.settings.cache_ttl_days.help": "Wie lange erfolgreiche GeoIP-Lookups gecacht werden, bevor sie erneuert werden.",
            "geoip.settings.timeout_seconds": "GeoIP-Timeout in Sekunden",
            "geoip.settings.timeout_seconds.help": "HTTP-Timeout für eine GeoIP-Provider-Anfrage.",
            "common.yes": "Ja",
            "common.no": "Nein",
        },
    }

    def __init__(self) -> None:
        self._last_cache_cleanup = 0.0
        self._next_event_before_id: int | None = None

    async def health(self, context: PluginContext) -> dict[str, str]:
        # Real cache outcomes keep this passive: the health loop cannot burn
        # provider quota or disclose a key or looked-up address.
        provider = context.get("provider", "iplocate")
        if provider == "iplocate":
            if not context.get("iplocate_api_key").strip():
                return {
                    "status": "error",
                    "message": "IPLocate is selected but no API key is configured: GeoIP lookups are skipped.",
                }
            transport_message = "GeoIP is active: uncached public IPs are sent to IPLocate's EU endpoint over encrypted HTTPS."
        elif provider == "ip-api":
            transport_message = "GeoIP is active: uncached public IPs are sent to ip-api.com over unencrypted HTTP."
        else:
            return {"status": "error", "message": f"Unsupported GeoIP provider: {provider}"}

        attempt = latest_provider_attempt(context.db, provider)
        if attempt is None:
            return {
                "status": "warning",
                "message": f"{transport_message} No GeoIP lookup has run yet, so provider reachability has not been verified.",
            }

        attempted_at = attempt.attempted_at.replace(tzinfo=None).isoformat(timespec="seconds")
        if attempt.error:
            if provider == "iplocate" and attempt.error in IPLOCATE_UNREACHABLE_ERRORS:
                return {
                    "status": "error",
                    "message": (
                        f"IPLocate was unreachable during the latest GeoIP lookup at {attempted_at} UTC. "
                        "GeoIP remains active and will retry uncached public IPs; check DNS, firewall, proxy, and outbound HTTPS."
                    ),
                }
            return {
                "status": "error",
                "message": (
                    f"The latest GeoIP lookup through {provider} failed at {attempted_at} UTC. "
                    "GeoIP remains active and will retry uncached public IPs; check the provider configuration and availability."
                ),
            }

        return {
            "status": "warning",
            "message": f"{transport_message} The latest GeoIP lookup succeeded at {attempted_at} UTC.",
        }

    async def enrich(self, context: PluginContext, limit: int) -> int:
        now = time.monotonic()
        if now - self._last_cache_cleanup >= CACHE_CLEANUP_INTERVAL_SECONDS:
            deleted = cleanup_expired_cache(context.db)
            if deleted:
                logger.debug("Removed %d expired GeoIP cache entries", deleted)
            self._last_cache_cleanup = now
        batch = enrich_pending_event_batch(context.db, limit, self._next_event_before_id)
        self._next_event_before_id = batch.next_before_id
        return batch.processed
