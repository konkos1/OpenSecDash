# ADR-013: Plugin Lifecycle & Plugin API

> **Implementation status (2026-07-09):** Implemented.
> Plugin API v2 uses package-based plugins, plugin manager orchestration, PluginContext, web hooks, IP panel hooks, duplicate rules, and action hooks.



This later determines whether someone can write an Nginx plugin in 30 minutes or whether 3 days of reverse engineering are required.


---

# Goal

A plugin should need to know as little as possible.

A plugin must not know:

```none
SQLite
Frontend
WebSockets
HTMX
Alpine
IP Explorer
```

A plugin should only know:

```none
I receive data
↓
I deliver events
```


---

# Datasource Plugin API

Example:

```python
class Plugin:
    id = "traefik"
    name = "Traefik Access Log"

    async def start(self):
        pass

    async def stop(self):
        pass

    async def collect(self):
        pass
```


---

The plugin creates events:

```python
yield Event(
    type="access",
    ip="1.2.3.4",
    data={}
)
```


---

# Event Pipeline

```none
Plugin
↓
Event
↓
Core
↓
SQLite
↓
Enrichment
↓
Insights
↓
WebSocket
↓
UI
```

**Important:**

The plugin only knows:

```none
Plugin
↓
Event
```

nothing more.


---

# Enrichment Lifecycle

Example:

`GeoIP Plugin`

Input:

```json
{
  "ip": "1.2.3.4"
}
```

Output:

```json
{
  "country": "DE",
  "asn": "AS3320"
}
```


---

Example:

`Reverse DNS Plugin`

```json
{
  "hostname": "crawl.example.com"
}
```


---

Multiple enrichment plugins may run one after another.

```none
Event
↓
GeoIP
↓
ASN
↓
DNS
↓
finished Event
```


---

# Action Plugin API

CrowdSec example:

```python
class CrowdSecBanAction:
    id = "crowdsec_ban"

    async def execute(
        self,
        ip: str,
        duration: str
    ):
        pass
```


---

UI only knows:

`Ban action available`

The rest is the plugin's responsibility.


---

# Plugin Manifest

Every plugin should have a metadata file.

Example:

```json
{
  "id": "traefik",
  "name": "Traefik",
  "version": "1.0.0",
  "type": "datasource"
}
```


---

This lets OpenSecDash automatically display:

```none
Plugins

✓ CrowdSec
✓ GeoIP
✓ Traefik
✗ Nginx
```


---

# Plugin Configuration

**Very important:**

Not:

```python
LOG_FILE="/var/log/..."
```


---

But:

```python
config.get("logfile")
```


---

**The configuration always comes from OpenSecDash.**

This later makes:

`Traefik`

and

`Nginx`

and

`Caddy`

work the same way.


---

# Plugin Installation

Keep it deliberately simple for V1.

Not:

* Plugin Store
* Marketplace


---

But for example:

```none
plugins/

├── crowdsec/
├── geoip/
├── traefik/
├── nginx/
```


---

At startup:

```none
Search plugin
↓
Read manifest
↓
Load
```


---

# Plugin Dependencies

Example:

`GeoIP needs database`

or

`CrowdSec needs a reachable LAPI`

Manifest:

```json
{
  "requires": [
    "crowdsec-lapi"
  ]
}
```


---

OpenSecDash shows:

```none
⚠ CrowdSec plugin not available

CrowdSec LAPI is not reachable
```


---

# Plugins may register their own pages.

Example:

`CrowdSec`

registers:

`/crowdsec`


---

`Traefik`

registers:

`/access`


---

`Assets`

registers:

`/assets`


---

This means the core later does not need to know:

```none
What is CrowdSec?
What is Pi-hole?
What is AdGuard?
```


---
