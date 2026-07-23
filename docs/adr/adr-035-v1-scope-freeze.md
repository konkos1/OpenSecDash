# ADR-035 = V1 Scope Freeze

> **Implementation status (2026-07-09):** Partially implemented.
> Most V1 scope areas exist. Torblock and some planned scope items are not implemented; Proxmox Assets is implemented although not part of the original V1 list.
> Scope decision (2026-07-11): Torblock is excluded from V1 (see implementation notes).


## OpenSecDash V1 includes

### Dashboard

* Active CrowdSec bans
* Geoblocks today
* Torblocks today
* Access events today
* Assets
* Updates available


---

### CrowdSec

* Active bans
* History
* Top scenarios
* Top countries
* IP ban
* IP unban


---

### Access

* Events
* Search
* Filters
* Live mode


---

### Events

* All events
* Filters
* Search


---

### IP Explorer

* All events for the IP
* CrowdSec bans
* Geoblocks
* Torblocks
* Access events
* Insights
* Manual ban


---

### Asset Explorer

* Systems
* Apps
* Versions
* Update status
* Release link


---

### Settings

* Language (DE/EN)
* Domain
* Logo/Favicon
* Data sources
* Retention
* Live defaults


---

### Diagnostics

* Plugin status
* Data source status
* Latest errors
* Support bundle


---

## V1 Core Plugins

### Datasources

* CrowdSec
* Access Logs
* GeoBlock
* Asset Inventory


---

### Enrichment

* GeoIP
* ASN


---

### Actions

* CrowdSec Ban
* CrowdSec Unban


---

### Export

* MQTT


---

# MQTT Plugin

## Goal

Publish events and states to external systems.


---

## Typical targets

Today:

`Home Assistant`


---

Later, for example in V2:

```none
Node-RED
OpenHAB
custom automations
```


---

# Not V1

Quite deliberately:

❌ User management

❌ Authentik Integration

❌ Telegram

❌ Discord

❌ Matrix

❌ Marketplace

❌ Plugin Store

❌ Update Installation

❌ Multi User

❌ Cluster

❌ AI Features

❌ OpenSearch

❌ PostgreSQL


---

---

## Implementation notes (2026-07-09)

The current implementation includes several V1 areas: Dashboard, CrowdSec, Access, Events, IP Explorer, Asset Explorer, Settings, Diagnostics, GeoIP/ASN enrichment, CrowdSec actions, MQTT export, JSON Assets, GitHub update detection, and Proxmox Assets.

Torblock is listed in earlier V1 planning but is not currently implemented as a plugin. Proxmox Assets is currently implemented even though it was not part of the original concise V1 core plugin list.

Scope decision (2026-07-11): Torblock remains excluded from V1. The Torblock entries above (Dashboard "Torblocks today", IP Explorer "Torblocks") do not apply to V1; consistent with this, the V1 core plugin list above never included a Torblock datasource.

Optional internal user management and multi-user roles were added after this scope decision.
The feature is disabled by default; see ADR-028.

## Implementation notes (2026-07-24)

The "Not V1" list above stays as written; it records the original scope freeze. Since
that freeze, internal user management, multi-user roles, and one optional OpenID Connect
sign-in method have been implemented anyway and are part of the shipped product.

As of this date they are no longer opt-in for new installations: a brand-new installation
starts with internal authentication enabled and guides the first visitor through a
one-time first-admin setup. Existing installations keep their current authentication
state across the upgrade — active installations continue unchanged, open ones stay
reachable and only receive a permanent security prompt. `OSD_AUTH_DISABLED` is the single
deliberate opt-out. See ADR-028 for the full decision, the boundary conditions, and the
upgrade behavior.
