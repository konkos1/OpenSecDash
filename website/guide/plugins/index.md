# Plugins

OpenSecDash is plugin-first. The core app provides storage, UI, settings, diagnostics, actions, and helper services. Plugins provide integrations.

![OpenSecDash plugin system](/assets/readme/plugin-system.svg)

Current plugins include:

| Plugin | Purpose |
| --- | --- |
| [CrowdSec](./crowdsec.md) | Import ban history and execute ban/unban actions through CrowdSec's Local API |
| [Traefik Access Log](./traefik.md) | Import and classify reverse proxy access log entries |
| [GeoBlock Log](./geoblock.md) | Import geoblock events |
| [GeoIP / ASN / ISP / City](./geoip.md) | Enrich public IP events |
| [JSON Assets](./json-assets.md) | Import systems and apps from JSON |
| [Proxmox Assets](./proxmox-assets.md) | Import Proxmox nodes, VMs/LXCs, and optional app declarations |
| [Asset update checks](./asset-update-checks.md) | Check known asset release URLs for updates |
| [MQTT to Home Assistant](./mqtt.md) | Publish app update status to Home Assistant |

Plugins define their own settings and translations. OpenSecDash renders them automatically on the Settings page.
