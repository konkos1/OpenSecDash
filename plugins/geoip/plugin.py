from __future__ import annotations

import logging

from app.plugins.base import PeriodicPlugin, PluginContext, PluginMetadata, PluginSetting
from app.services.geoip import cleanup_expired_cache

logger = logging.getLogger(__name__)


class Plugin(PeriodicPlugin):
    metadata = PluginMetadata(
        id="geoip",
        name="GeoIP / ASN Enrichment",
        version="1.0.0",
        capabilities=["enrichment"],
        description="Adds country codes and ASNs to public IP events using a cached provider lookup.",
    )
    settings = [
        PluginSetting("enabled", "geoip.settings.enabled", "geoip.settings.enabled.help", "boolean", "true", [("false", "common.no"), ("true", "common.yes")]),
        PluginSetting("provider", "geoip.settings.provider", "geoip.settings.provider.help", "select", "ip-api", [("ip-api", "geoip.option.ip_api")], visible_if=("enabled", "true")),
        PluginSetting("cache_ttl_days", "geoip.settings.cache_ttl_days", "geoip.settings.cache_ttl_days.help", "number", "30", visible_if=("enabled", "true")),
        PluginSetting("timeout_seconds", "geoip.settings.timeout_seconds", "geoip.settings.timeout_seconds.help", "number", "3", visible_if=("enabled", "true")),
    ]
    locales = {
        "en": {
            "geoip.settings.enabled": "GeoIP/ASN enrichment enabled",
            "geoip.settings.enabled.help": "Adds country code and ASN to new public-IP events when the producer did not already provide them.",
            "geoip.settings.provider": "GeoIP provider",
            "geoip.settings.provider.help": "Provider used for lookups. ip-api works without an API key but uses the public free endpoint.",
            "geoip.option.ip_api": "ip-api.com",
            "geoip.settings.cache_ttl_days": "GeoIP cache TTL days",
            "geoip.settings.cache_ttl_days.help": "How long successful GeoIP lookups stay cached before being refreshed.",
            "geoip.settings.timeout_seconds": "GeoIP timeout seconds",
            "geoip.settings.timeout_seconds.help": "HTTP timeout for one GeoIP provider request.",
            "common.yes": "Yes",
            "common.no": "No",
        },
        "de": {
            "geoip.settings.enabled": "GeoIP/ASN-Anreicherung aktiviert",
            "geoip.settings.enabled.help": "Ergänzt bei neuen Events mit öffentlicher IP Länder-Code und ASN, wenn der Erzeuger diese noch nicht geliefert hat.",
            "geoip.settings.provider": "GeoIP-Provider",
            "geoip.settings.provider.help": "Provider für Lookups. ip-api funktioniert ohne API-Key, nutzt aber den öffentlichen Free-Endpunkt.",
            "geoip.option.ip_api": "ip-api.com",
            "geoip.settings.cache_ttl_days": "GeoIP-Cache-TTL in Tagen",
            "geoip.settings.cache_ttl_days.help": "Wie lange erfolgreiche GeoIP-Lookups gecacht werden, bevor sie erneuert werden.",
            "geoip.settings.timeout_seconds": "GeoIP-Timeout in Sekunden",
            "geoip.settings.timeout_seconds.help": "HTTP-Timeout für eine GeoIP-Provider-Anfrage.",
            "common.yes": "Ja",
            "common.no": "Nein",
        },
    }

    async def health(self, context: PluginContext) -> dict[str, str]:
        provider = context.get("provider", "ip-api")
        if provider != "ip-api":
            return {"status": "error", "message": f"Unsupported GeoIP provider: {provider}"}
        return {"status": "healthy", "message": f"GeoIP provider configured: {provider}"}

    async def tick(self, context: PluginContext) -> None:
        deleted = cleanup_expired_cache(context.db)
        if deleted:
            logger.debug("Removed %d expired GeoIP cache entries", deleted)
