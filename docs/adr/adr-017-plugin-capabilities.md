# ADR-017: Plugin Capabilities

> **Implementation status (2026-07-09):** Implemented.
> Plugins declare capabilities through PluginMetadata; plugin manager stores records and uses capabilities/hooks for web, actions, asset sources, exports, and more.



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
