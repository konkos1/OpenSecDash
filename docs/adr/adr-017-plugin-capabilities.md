# ADR-017: Plugin Capabilities

> **Implementation status (2026-08-02):** Implemented.
> Plugins declare capabilities and the event types they produce through
> `PluginMetadata`. The plugin manager publishes this metadata through the core
> registry so Insight and notification availability can be derived without the
> core importing integration-specific plugin code.



## Goal

A plugin should explicitly declare what it can do.

Not:

```none
Load plugin
↓
Hope it works
```

But:

```none
Load plugin 
↓ 
Check capabilities 
↓ 
Core knows exactly what is available
```


---

# Capability types

A plugin can have one or more capabilities.

## datasource

Provides events.

Examples:

```none
Traefik 
Nginx 
Caddy 
CrowdSec
```


---

## enrichment

Enriches events.

Examples:

```none
GeoIP
ASN
Reverse DNS
```


---

## action

Executes actions.

Examples:

```none
CrowdSec Ban 
Webhook
```


---

## page

Registers its own pages.

Examples:

```none
CrowdSec
Assets
```


---

## widget

Registers dashboard widgets.

Examples:

```none
Top countries
Active bans
Top scenarios
```


---

## insight

Generates insights.

Example:

```none
404
+
Geoblock

=
Probably caused by geoblock
```


---

# Manifest

Example:

```json
{
  "id": "crowdsec",
  "name": "CrowdSec",
  "version": "1.0.0",
  "capabilities": [
    "datasource",
    "action",
    "page",
    "widget"
  ]
}
```

Datasource and action plugins additionally declare their emitted event types:

```python
PluginMetadata(
    id="my_firewall",
    name="My Firewall",
    capabilities=["datasource"],
    event_types=["security.firewall_block"],
)
```

These declarations describe possible output, not current health. Core features
combine them with the plugin's enabled state when deciding whether an Insight or
notification rule is currently available. A temporary plugin error therefore does
not discard configuration, while disabling or unloading the last producer makes
dependent rules unavailable.


---

# Dashboard Widgets

`CrowdSec Plugin`

provides:

`Active bans`

`Top countries`


---

`Geoblock Plugin`

provides:

`Top countries`


---

`Assets Plugin`

provides:

`Installed apps`


---

The core does not need to know:

```none
What is CrowdSec?
What is Geoblock?
```


---

# Page registration

Plugin:

```python
register_page(
    "/crowdsec",
    CrowdSecPage
)
```


---

Plugin:

```python
register_page(
    "/assets",
    AssetsPage
)
```


---

This makes the menu dynamic.


---

# Dashboard configuration

The user can later decide, for example:

```python
Dashboard

☑ Active bans
☑ Top countries
☑ Latest geoblocks
☑ Installed apps

☐ Torblock
☐ Fail2Ban
```


---
