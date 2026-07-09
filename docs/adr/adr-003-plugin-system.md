# ADR-003: Plugin System

> **Implementation status (2026-07-09):** Implemented.
> Plugin capabilities cover datasource, asset_source, enrichment, action, export, page, widget, and insight. Current plugins include CrowdSec, Traefik log, GeoBlock log, GeoIP, JSON Assets, Proxmox Assets, and MQTT.



## Plugin types

### Datasource

Reads data.

Examples:

`Traefik`
`Nginx`
`Caddy`
`CrowdSec`


---

### Enrichment

Extends data.

Examples:

`GeoIP`
`ASN`
`DNS`


---

### Action

Executes actions.

Examples:

`CrowdSec Ban`
`CrowdSec Unban`
`Webhook`


---

### Export

Exports data.

Examples:

`MQTT`
`Webhook`
`JSON Export`


---

---

## Implementation notes (2026-07-09)

The current plugin API is capability-based. In addition to the original categories, the implemented capabilities include:

```none
datasource
asset_source
enrichment
action
export
page
widget
insight
```

Currently implemented plugin packages are:

```none
crowdsec
traefik_log
geoblock_log
geoip
json_assets
proxmox_assets
mqtt
```

