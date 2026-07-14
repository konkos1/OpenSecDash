# Plugins

OpenSecDash is plugin-first. The core app provides storage, UI, settings, diagnostics, the Action Framework, notification services, and safe extension points. Plugins provide integrations and can contribute datasource events, actions, dashboard widgets, IP Explorer panels, saved views, and validated declarative Insight rules.

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

Plugins define their own settings and translations. OpenSecDash renders them automatically on the Settings page. Plugin-provided UI data is validated and rendered by core templates; plugins do not inject arbitrary dashboard HTML or remote executable rules.
