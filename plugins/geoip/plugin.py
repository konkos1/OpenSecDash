from __future__ import annotations

import logging

from app.plugins.base import PeriodicPlugin, PluginContext, PluginMetadata, PluginSetting
from app.services.geoip import cleanup_expired_cache

logger = logging.getLogger(__name__)


class Plugin(PeriodicPlugin):
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
        PluginSetting("provider", "geoip.settings.provider", "geoip.settings.provider.help", "select", "ip-api", [("ip-api", "geoip.option.ip_api")], visible_if=("enabled", "true")),
        PluginSetting("cache_ttl_days", "geoip.settings.cache_ttl_days", "geoip.settings.cache_ttl_days.help", "number", "30", visible_if=("enabled", "true")),
        PluginSetting("timeout_seconds", "geoip.settings.timeout_seconds", "geoip.settings.timeout_seconds.help", "number", "3", visible_if=("enabled", "true")),
    ]
    locales = {
        "en": {
            "geoip.settings.enabled": "GeoIP enabled (sends uncached public IPs to ip-api.com over unencrypted HTTP)",
            "geoip.settings.enabled.help": "Warning: each uncached public IP is sent over unencrypted HTTP to ip-api.com. Successful results are cached for the configured TTL; failures for one hour. Private and reserved IPs are never sent.",
            "geoip.settings.provider": "GeoIP provider",
            "geoip.settings.provider.help": "ip-api.com receives the public IP over its unencrypted free HTTP endpoint. OpenSecDash does not treat this transport as secure.",
            "geoip.option.ip_api": "ip-api.com",
            "geoip.settings.cache_ttl_days": "GeoIP cache TTL days",
            "geoip.settings.cache_ttl_days.help": "How long successful GeoIP lookups stay cached before being refreshed.",
            "geoip.settings.timeout_seconds": "GeoIP timeout seconds",
            "geoip.settings.timeout_seconds.help": "HTTP timeout for one GeoIP provider request.",
            "common.yes": "Yes",
            "common.no": "No",
        },
        "de": {
            "geoip.settings.enabled": "GeoIP aktiviert (sendet nicht gecachte öffentliche IPs unverschlüsselt an ip-api.com)",
            "geoip.settings.enabled.help": "Warnung: Jede nicht gecachte öffentliche IP wird unverschlüsselt per HTTP an ip-api.com gesendet. Erfolge werden für die konfigurierte TTL, Fehler eine Stunde gecacht. Private und reservierte IPs werden nie gesendet.",
            "geoip.settings.provider": "GeoIP-Provider",
            "geoip.settings.provider.help": "ip-api.com erhält die öffentliche IP über seinen unverschlüsselten kostenlosen HTTP-Endpunkt. OpenSecDash stuft diesen Transport nicht als sicher ein.",
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
        return {
            "status": "warning",
            "message": "GeoIP is active: uncached public IPs are sent to ip-api.com over unencrypted HTTP.",
        }

    async def tick(self, context: PluginContext) -> None:
        deleted = cleanup_expired_cache(context.db)
        if deleted:
            logger.debug("Removed %d expired GeoIP cache entries", deleted)
