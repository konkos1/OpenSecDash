# Plugins

OpenSecDash is plugin-first. The core app provides storage, UI, settings, diagnostics, actions, and helper services. Plugins provide integrations.

Current plugins include:

| Plugin | Purpose |
| --- | --- |
| CrowdSec | Import ban history and execute ban/unban actions via `cscli` |
| Traefik Access Log | Import and classify reverse proxy access log entries |
| GeoBlock Log | Import geoblock events |
| GeoIP / ASN / ISP / City | Enrich public IP events |
| Apps Inventory JSON | Import systems and apps from JSON |
| Proxmox Assets | Import Proxmox nodes, VMs/LXCs, and optional app declarations |
| MQTT to Home Assistant | Publish app update status to Home Assistant |

Plugins define their own settings and translations. OpenSecDash renders them automatically on the Settings page.
